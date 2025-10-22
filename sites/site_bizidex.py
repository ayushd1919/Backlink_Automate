# sites/site_bizidex.py
from sites.base import BaseSite
from core.mail import MailUnavailable
from core.utils import rand_password
from playwright.sync_api import TimeoutError as PWTimeout

import os, json, re

DIR = "https://bizidex.com"
ART_DIR = os.path.join("artifacts", "bizidex")

class VerificationTimeout(Exception): ...
class AlreadyExists(Exception): ...
class AccountNotFound(Exception): ...
class WrongPassword(Exception): ...

class SiteBizidex(BaseSite):
    """
    Bizidex adapter (login-first, no pre-profile navigation):

      - Detect logged-in state from HOME HEADER only (no /account-profile probes).
      - On /login: if Register/Sign up CTA exists, click it; else try to log in.
      - Registration -> manual CAPTCHA pause -> email verification -> login.
      - Artifacts (username/password) per email.
      - Listing flow: ensure_logged_in() -> My Listing -> Edit/Create -> add website -> Save/Publish -> View Listing.
    """
    name = "bizidex.com"
    requires_email_verification = True

    def __init__(self, headed=False):
        super().__init__(headed=headed)
        self.creds = {"email": None, "username": None, "password": None}
        self._logged_in = False

    # ---------- artifacts ----------
    def _artifact_path(self, email: str) -> str:
        safe = (email or "unknown").replace("/", "_").replace("\\", "_")
        return os.path.join(ART_DIR, f"{safe}.json")

    def _load_saved_creds(self, email: str) -> dict | None:
        try:
            pth = self._artifact_path(email)
            if os.path.exists(pth):
                with open(pth, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("account_email") == email and data.get("password"):
                    return data
        except Exception:
            pass
        return None

    def _save_creds(self):
        try:
            os.makedirs(ART_DIR, exist_ok=True)
            with open(self._artifact_path(self.creds["email"]), "w", encoding="utf-8") as f:
                json.dump(self.get_creds_snapshot(), f, indent=2)
            self.log.log(self.name, "artifacts", "saved_creds", self._artifact_path(self.creds["email"]))
        except Exception as e:
            self.log.log(self.name, "artifacts", "save_failed", str(e))

    # ---------- public API ----------
    def set_creds(self, account_email: str, username: str | None, password: str | None):
        self.creds["email"] = account_email
        self.creds["username"] = (username or account_email.split("@")[0])[:30]
        self.creds["password"] = password or rand_password(12)
        saved = self._load_saved_creds(account_email)
        if saved:
            self.creds["username"] = saved.get("username") or self.creds["username"]
            self.creds["password"] = saved.get("password") or self.creds["password"]
            self.log.log(self.name, "artifacts", "loaded_creds", f"{self.creds['username']}/{account_email}")
        else:
            self.log.log(self.name, "artifacts", "no_saved_creds", account_email)

    def get_creds_snapshot(self) -> dict:
        return {"account_email": self.creds["email"], "username": self.creds["username"], "password": self.creds["password"]}

    def register_or_login_with_verification(self):
        # If already logged in according to header, stop early.
        if self._session_looks_logged_in():
            self._logged_in = True
            self._save_creds()
            self.log.log(self.name, "session", "already_logged_in", self.creds["email"])
            return

        # Open /login and CLICK Register if present (your requested behavior)
        self.browser.goto(f"{DIR}/login")
        if self._click_register_cta_if_visible():
            try:
                self._register()
                self.verify_email()
                self.login(self.creds["email"], self.creds["password"])
                self._save_creds()
                return
            except AlreadyExists:
                pass

        # If registration wasn't done or it existed, log in
        try:
            self.login(self.creds["email"], self.creds["password"])
            self._save_creds()
            return
        except AccountNotFound:
            # Fallback to direct /register
            self._register()
            self.verify_email()
            self.login(self.creds["email"], self.creds["password"])
            self._save_creds()
            return

    # ---------- session guards & url helpers ----------
    def ensure_logged_in(self):
        """
        Ensure we are authenticated using header-based checks only.
        Never navigates to profile pages here.
        """
        if self._session_looks_logged_in():
            return

        # First attempt: login
        try:
            self.login(self.creds["email"], self.creds["password"])
        except AccountNotFound:
            # Register (from /login CTA if available) -> verify -> login
            try:
                self._click_register_cta_if_visible()
            except Exception:
                pass
            self._register()
            self.verify_email()
            self.login(self.creds["email"], self.creds["password"])
        except WrongPassword:
            raise

        # Re-check session via header signals on home
        if not self._session_looks_logged_in():
            # As a last resort, refresh home and try login once more
            try:
                self.login(self.creds["email"], self.creds["password"])
            except Exception:
                pass

        if not self._session_looks_logged_in():
            raise RuntimeError("Login did not stick; session headers do not indicate an authenticated user.")

        self._save_creds()

    def _normalize_url(self, href: str) -> str:
        """Return an absolute URL under DIR for any relative path."""
        if not href:
            return DIR
        h = href.strip()
        if h.startswith("http://") or h.startswith("https://"):
            return h
        if h.startswith("/"):
            return DIR.rstrip("/") + h
        return DIR.rstrip("/") + "/" + h.lstrip("/")

    # ---------- header-based session check ----------
    def _session_looks_logged_in(self) -> bool:
        """
        Avoid navigating to /account-profile to probe login.
        Open HOME and look for header elements that only appear when logged in.
        """
        p = self.browser.page
        try:
            self.browser.goto(DIR)
            p.wait_for_load_state("domcontentloaded")
            # Typical logged-in header signals (adjust as needed on your live header):
            return (
                p.is_visible("a[href='/account-profile']") or
                p.is_visible("a[href='/account-profile/listing']") or
                p.is_visible("a:has-text('Dashboard')") or
                p.is_visible(".user-status .dropdown-toggle .user-name") or
                p.is_visible("a:has-text('Logout'), a[href*='logout']")
            )
        except Exception:
            return False

    # ---------- auth ----------
    def login(self, email: str, password: str):
        p = self.browser.page
        self.browser.goto(f"{DIR}/login")
        p.wait_for_load_state("domcontentloaded")

        form = self._find_login_form()
        if not form:
            raise RuntimeError("Could not locate login form on /login.")

        # Identifier
        if not self._fill_first_visible(form,
            ["input[type='email']", "input[name='email']", "input[name='user_email']"],
            str(email)
        ):
            self._fill_first_visible(form,
                ["input[name='username']", "input[name='log']", "input[type='text']"],
                str(self.creds["username"] or email)
            )

        # Password
        self._fill_first_visible(form,
            ["input[type='password']", "input[name='password']", "input[name='pwd']"],
            str(password)
        )

        self.log.log(self.name, "login", "attempt", email)

        # Submit THIS form
        submitted = False
        try:
            btn = form.locator("button[type='submit'], input[type='submit']").first
            if btn and btn.is_visible():
                with p.expect_navigation(wait_until="load"):
                    btn.click()
                submitted = True
        except Exception:
            pass
        if not submitted:
            try:
                pw = form.locator("input[type='password'], input[name='password'], input[name='pwd']").first
                if pw and pw.is_visible():
                    with p.expect_navigation(wait_until="load"):
                        pw.press("Enter")
                    submitted = True
            except Exception:
                pass
        if not submitted:
            raise RuntimeError("Login submit failed (no submit control inside login form).")

        # Success?
        if self._session_looks_logged_in():
            self._logged_in = True
            self.log.log(self.name, "login", "success", email)
            return

        # Parse page text for errors
        body = (p.text_content("body") or "").lower()
        if any(k in body for k in ["no account", "not found", "register", "create account"]):
            raise AccountNotFound("Account not found")
        if any(k in body for k in ["wrong password", "incorrect password", "invalid password"]):
            raise WrongPassword("Password incorrect")
        raise RuntimeError("Login failed: no success indicators and no clear error text.")

        def _click_register_cta_if_visible(self) -> bool:
            """
            On /login, click the 'Register / Create new account' link robustly.
            We check the main form area, header, and footer. If clicking doesn't
            navigate to /register, we force a goto as a fallback.
            """
            p = self.browser.page

            # We expect to be on /login. If not, go there first.
            if "/login" not in (p.url or ""):
                self.browser.goto(f"{DIR}/login")
                p.wait_for_load_state("domcontentloaded")

            # 1) In-form CTA (the one shown under the submit button)
            #    <p>You Don't have any account? <a href="/register">Register</a></p>
            candidates = [
                # ARIA role best-effort
                ("role", "link", r"register"),
                # Scoped to common in-form containers
                ("css", "form .signup-screen-single a:has-text('Register')", None),
                ("css", "form a[href='/register']", None),
                ("css", "form a:has-text('Create new account')", None),
                # Header buttons (Add Listing -> /register also present)
                ("css", "a[href='/register']", None),
                ("css", "a:has-text('Add Listing')", None),
                # Footer “My Account” -> Register
                ("css", "footer a[href='/register']", None),
                # Last resort: any anchor that literally contains 'register'
                ("css", "a[href*='register']", None),
            ]

            # Try each candidate in order. As soon as one succeeds, verify URL.
            for kind, sel, name_pattern in candidates:
                try:
                    if kind == "role":
                        # Playwright role query with case-insensitive name
                        link = p.get_by_role("link", name=re.compile(name_pattern, re.I))
                    else:
                        link = p.locator(sel)
                    if link and link.first.is_visible():
                        # Scroll into view to avoid overlay intercepts
                        link.first.scroll_into_view_if_needed(timeout=2000)
                        with p.expect_navigation(wait_until="load"):
                            link.first.click()
                        # Nuxt may show same URL briefly; give it a heartbeat
                        p.wait_for_timeout(250)
                        if "/register" in (p.url or ""):
                            return True
                    # If click didn't navigate (SPA swallow), try programmatic href
                    if link and link.first.is_visible():
                        href = link.first.get_attribute("href")
                        if href:
                            with p.expect_navigation(wait_until="load"):
                                p.goto(self._normalize_url(href))
                            if "/register" in (p.url or ""):
                                return True
                except Exception:
                    continue

            # Fallback: force open /register
            try:
                with p.expect_navigation(wait_until="load"):
                    p.goto(f"{DIR}/register")
                if "/register" in (p.url or ""):
                    return True
            except Exception:
                pass

            return False

    def _safe_click_link(self, locator_str: str) -> bool:
        """
        Legacy helper (optional): click a locator and verify navigation occurred.
        """
        p = self.browser.page
        try:
            loc = p.locator(locator_str).first
            if loc and loc.is_visible():
                loc.scroll_into_view_if_needed(timeout=1500)
                with p.expect_navigation(wait_until="load"):
                    loc.click()
                return True
        except Exception:
            pass
        return False

    def _find_login_form(self):
        p = self.browser.page
        try:
            f = p.locator("form").filter(has=p.locator("input[type='password']")).first
            if f and f.is_visible(): return f
        except Exception:
            pass
        try:
            f = p.locator(
                "form:has(input[type='password']):has(input[type='email'], input[name='email'], input[name='user_email'], input[name='username'], input[name='log'], input[type='text'])"
            ).first
            if f and f.is_visible(): return f
        except Exception:
            pass
        return None

    # ---------- registration ----------
    def _register(self):
        p = self.browser.page
        if "/register" not in (p.url or ""):
            self.browser.goto(f"{DIR}/register")
        p.wait_for_load_state("domcontentloaded")

        form = self._find_register_form()
        if not form:
            raise RuntimeError("Could not locate registration form on /register.")

        email = self.creds["email"]
        username = self.creds["username"]
        pw = self.creds["password"]

        # common fields
        self._fill_first_visible(form, ["input[name='name']", "input[placeholder*='name' i]", "#name"], self._name_from_email(email))
        self._fill_first_visible(form, ["#user_login", "input[name='user_login']", "input[placeholder*='username' i]"], username)
        self._fill_first_visible(form, ["#user_email", "input[name='user_email']", "input[type='email']", "input[name='email']"], email)

        # passwords (usually two)
        pw_inputs = form.locator("input[type='password']")
        try:
            cnt = pw_inputs.count()
        except Exception:
            cnt = 0
        if cnt >= 2:
            try:
                pw_inputs.nth(0).fill(pw)
                pw_inputs.nth(1).fill(pw)
            except Exception:
                for i in range(cnt):
                    try:
                        node = pw_inputs.nth(i)
                        if node.is_visible(): node.fill(pw)
                    except Exception:
                        pass
        else:
            self._fill_first_visible(form, ["#pass1", "input[name='pass1']", "input[id*='pass1' i]"], pw)
            self._fill_first_visible(form, ["#pass2", "input[name='pass2']", "input[id*='pass2' i]"], pw)

        # optional manual captcha
        try:
            self.browser.wait_manual_captcha("\nSolve any CAPTCHA on Register, then press Enter here to submit…")
        except Exception:
            pass

        # submit this form only
        submitted = False
        try:
            btn = form.locator("button[type='submit'], input[type='submit'], button:has-text('Register'), input[value*='Register' i]").first
            if btn and btn.is_visible():
                with p.expect_navigation(wait_until="load"):
                    btn.click()
                submitted = True
        except Exception:
            pass
        if not submitted:
            try:
                last_pw = form.locator("input[type='password']").last
                if last_pw and last_pw.is_visible():
                    with p.expect_navigation(wait_until="load"):
                        last_pw.press("Enter")
                    submitted = True
            except Exception:
                pass
        if not submitted:
            raise RuntimeError("Could not submit registration form.")

        # inspect for 'already exists'
        body = (p.text_content("body") or "").lower()
        if any(k in body for k in ["already registered", "already exists", "email address is already", "username already", "user already exists"]):
            self.log.log(self.name, "register", "exists", f"{username}/{email}")
            raise AlreadyExists("Already exists")

        self.log.log(self.name, "register", "submitted", email)

    def _find_register_form(self):
        p = self.browser.page
        try:
            reg = p.locator("form").filter(has=p.locator("#user_email, input[name='user_email'], input[type='email']")).first
            if reg and reg.is_visible(): return reg
        except Exception:
            pass
        try:
            reg = p.locator("form:has(#user_login):has(input[type='password'])").first
            if reg and reg.is_visible(): return reg
        except Exception:
            pass
        return None

    # ---------- email verification ----------
    def verify_email(self):
        """
        Fetch verification link (Spam included by MailClient), open it,
        and ensure we're logged in afterwards.
        """
        link = None
        try:
            link = self.mail.wait_for_verification_link(
                subject_hint="Bizidex",
                timeout_sec=600,
                prefer_domain="bizidex.com",
            )
        except MailUnavailable as e:
            self.log.log(self.name, "email", "mail_unavailable", str(e))
        if not link:
            print("\n[Mail] Could not fetch verification link automatically.")
            url = input("Paste the Bizidex verification URL here and press Enter: ").strip()
            if not url:
                raise VerificationTimeout("No verification link provided and mail fetch unavailable.")
            link = url

        self.log.log(self.name, "email", "verification_link", link)
        self.browser.goto(link)
        self.browser.page.wait_for_load_state("domcontentloaded")

        # Ensure authenticated after verification (header-based)
        if not self._session_looks_logged_in():
            try:
                self.login(self.creds["email"], self.creds["password"])
                self._save_creds()
            except Exception:
                pass

    # ---------- listing ops ----------
    def complete_profile_and_add_website_then_publish(self, website:str) -> str:
        """
        ensure_logged_in() -> My Listing -> Edit/Create -> add website -> Save/Publish -> View Listing -> return URL.
        """
        p = self.browser.page

        # MUST be logged in before touching profile pages
        self.ensure_logged_in()

        # Open My Listing; if the site bounced to /login, retry once after re-auth
        self.browser.goto(f"{DIR}/account-profile/listing")
        p.wait_for_load_state("domcontentloaded")
        if "/login" in (p.url or ""):
            self.ensure_logged_in()
            self.browser.goto(f"{DIR}/account-profile/listing")
            p.wait_for_load_state("domcontentloaded")

        # Click Edit on the latest listing, else open first listing link, else 'Create'
        edit_btn = p.locator("a:has-text('Edit'), button:has-text('Edit'), a[href*='edit']").first
        if edit_btn and edit_btn.is_visible():
            with p.expect_navigation(wait_until="load"):
                edit_btn.click()
        else:
            first_link = p.locator("a[href*='/account-profile/listing/'], a[href*='/edit'], a[href*='/listing/']").first
            href = first_link.get_attribute("href") if first_link and first_link.is_visible() else None
            if href:
                with p.expect_navigation(wait_until="load"):
                    p.goto(self._normalize_url(href))
            else:
                create = p.locator("a[href*='/account-profile/listing/new'], a:has-text('Create'), button:has-text('Create')").first
                if create and create.is_visible():
                    href = create.get_attribute("href")
                    with p.expect_navigation(wait_until="load"):
                        if href:
                            p.goto(self._normalize_url(href))
                        else:
                            create.click()

        # Edit/Create page
        p.wait_for_load_state("domcontentloaded")

        # Company name
        self._fill_first_visible(p, [
            "input[name='company_name']",
            "input[id*='company' i]",
            "input[placeholder*='company' i]"
        ], self._default_company_from_email(self.creds["email"]))

        # Category dropdown
        self._try_select_like(
            "#vs2__combobox, .v-select .vs__dropdown-toggle, select[name='category'], #category",
            "li[id*='vs'], .vs__dropdown-menu li, option[value]:not([value=''])"
        )

        # Country
        self._try_select_like(
            "#vs1__combobox, .v-select[name='countryName'] .vs__dropdown-toggle, select[name='country'], #country",
            "li[id*='vs'], .vs__dropdown-menu li, option[value]:not([value=''])"
        )

        # Phone
        self._fill_first_visible(p, [
            "input[name='phone']",
            "input[type='tel']",
            "input[id*='phone' i]"
        ], "020 7946 0000")

        # Website
        self._fill_first_visible(p, [
            "input[name='website']",
            "input[id*='website' i]",
            "input[name*='url' i]",
            "input[placeholder*='website' i]"
        ], website)

        # Save / Publish
        saved = False
        for sel in [
            "button:has-text('Publish')", "button:has-text('Save')",
            "input[type='submit'][value*='Publish' i]", "input[type='submit'][value*='Save' i]"
        ]:
            try:
                if p.is_visible(sel):
                    with p.expect_navigation(wait_until="load"):
                        p.click(sel)
                    saved = True
                    break
            except Exception:
                continue
        if not saved:
            try:
                with p.expect_navigation(wait_until="load"):
                    p.press("form >> input, form >> textarea", "Enter")
                saved = True
            except Exception:
                pass

        # Try to open a public "View Listing" link
        view_candidates = p.locator("a:has-text('View Listing'), a[href*='/listing/'], a[href*='/listings/']")
        best = None
        try:
            count = view_candidates.count()
        except Exception:
            count = 0

        for i in range(count):
            a = view_candidates.nth(i)
            href = a.get_attribute("href") or ""
            if not href:
                continue
            href = self._normalize_url(href)
            if "/login" in href or "logged_out" in href:
                continue
            best = href
            break

        if not best:
            # fallback: go back to My Listing and open the first public listing
            self.browser.goto(f"{DIR}/account-profile/listing")
            p.wait_for_load_state("domcontentloaded")
            public = p.locator("a[href*='/listing/'], a[href*='/listings/']").first
            href = public.get_attribute("href") if public and public.is_visible() else None
            if href:
                best = self._normalize_url(href)

        if best:
            with p.expect_navigation(wait_until="load"):
                p.goto(best)

        # Return public URL
        if any(k in (p.url or "") for k in ("/listing", "/listings")):
            self.log.log(self.name, "listing", "published", p.url)
            return p.url
        raise RuntimeError("Could not navigate to public listing URL after publish.")

    # ---------- helpers ----------
    def _fill_first_visible(self, scope, selectors: list[str], value: str) -> bool:
        for sel in selectors:
            try:
                node = scope.locator(sel).first
                if node and node.is_visible():
                    node.fill("")
                    node.fill(value)
                    node.evaluate("(el)=>{el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); el.blur && el.blur();}")
                    return True
            except Exception:
                continue
        return False

    def _try_select_like(self, open_selector: str, option_selector: str):
        p = self.browser.page
        try:
            if p.is_visible(open_selector):
                p.click(open_selector)
                p.wait_for_timeout(250)
                opt = p.locator(option_selector).first
                if opt and opt.is_visible():
                    opt.click()
                else:
                    sel = p.locator("select").first
                    if sel and sel.is_visible():
                        sel.select_option(index=1)
        except Exception:
            pass

    def _name_from_email(self, email: str) -> str:
        base = email.split("@")[0]
        base = re.sub(r"[._-]+", " ", base).strip().title()
        return base or "User"

    def _default_company_from_email(self, email: str) -> str:
        name = self._name_from_email(email)
        return f"{name} Solutions"
