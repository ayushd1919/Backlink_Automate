from sites.base import BaseSite
from core.utils import rand_username, rand_password
from core.formfill import fill_contact_fields
import re, time

BASE = "https://www.yplocal.com"

class AlreadyExistsError(Exception): ...
class WrongPasswordError(Exception): ...
class VerificationNotFound(Exception): ...

# Keep pattern for docs; we no longer pass regex to MailClient (your MailClient doesn't accept it)
VERIFY_RE = re.compile(r"https://www\.yplocal\.com/signup/verify/[A-Za-z0-9]+", re.I)

class SiteYPLocal(BaseSite):
    """
    Flow:
      - register()  (fills BOTH email fields + BOTH password fields, then prompts for CAPTCHA)
      - verify_email()  (reads mailbox via core.mail, opens link, ensures session)
      - login()
      - fill_contact_required_only()
      - fill_listing_resume(website)
      - go_dashboard_and_open_listing() -> return public URL
    """
    name = "yplocal"
    requires_email_verification = True

    def __init__(self, headed=False):
        super().__init__(headed=headed)
        self.creds = {"username": None, "password": None, "email": None}
        self._logged_in = False

    # ---------- public api ----------
    def set_creds(self, account_email: str, username: str | None, password: str | None):
        self.creds["email"]    = account_email
        self.creds["username"] = username or rand_username("yp")
        self.creds["password"] = password or rand_password(12)

    def get_creds_snapshot(self) -> dict:
        return {
            "account_email": self.creds["email"],
            "username": self.creds["username"],
            "password": self.creds["password"],
        }

    def register_or_login_with_verification(self):
        # If already logged in, skip
        if self._try_detect_logged_in():
            self._logged_in = True
            self.log.log(self.name, "session", "already_logged_in", self.creds["username"])
            return

        # --- Register → email verify → login ---
        self.browser.goto(f"{BASE}/checkout/3")
        self._register_flow()
        self.verify_email()
        self.login(self.creds["email"], self.creds["password"])

    # Compatibility alias so dispatcher can call a single name
    def register_or_login_with_recovery(self):
        return self.register_or_login_with_verification()

    def login(self, user: str, password: str):
        p = self.browser.page
        self.browser.goto(f"{BASE}/login")

        # clear then fill (avoids half-typed values)
        if p.is_visible("#username"):
            p.fill("#username", "")
            p.fill("#username", str(user))
        if p.is_visible("#password"):
            p.fill("#password", "")
            p.fill("#password", str(password))

        # submit
        clicked = False
        for sel in ["button[type=submit]", "input[type=submit]", "button:has-text('Login')", "button:has-text('Sign In')"]:
            try:
                if p.locator(sel).first.is_visible():
                    with p.expect_navigation(wait_until="load"):
                        p.locator(sel).first.click()
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked and p.is_visible("#password"):
            with p.expect_navigation(wait_until="load"):
                p.press("#password", "Enter")

        self.log.log(self.name, "login", "attempt", str(user))

        if "/login" in p.url.lower():
            body = (p.text_content("body") or "").lower()
            if "incorrect" in body or "invalid" in body:
                raise WrongPasswordError("Invalid credentials")
            raise RuntimeError("Login did not succeed (still on /login).")

        if not self._try_detect_logged_in():
            raise RuntimeError("Login did not succeed (account pages not accessible).")

        self._logged_in = True

    def verify_email(self, timeout_sec: int = 420):
        """
        Poll mailbox for a verification email from yplocal and click the /signup/verify/ link.
        Your MailClient signature is wait_for_verification_link(subject_hint=..., timeout_sec=...),
        so we only pass supported args.
        """
        # IMPORTANT CHANGE: removed unsupported 'regex=' kwarg
        link = self.mail.wait_for_verification_link(
            subject_hint="yplocal",
            timeout_sec=timeout_sec,
        )
        if not link:
            # Fallback: manual paste
            self.log.log(self.name, "verify_email", "not_found", "fallback_manual")
            url = input("\nPaste the yplocal verification URL here (from email) and press Enter: ").strip()
            if not url:
                raise VerificationNotFound("No verification link located.")
            link = url

        self.browser.goto(link)
        self.log.log(self.name, "verify_email", "opened", link)

        # Optional confirm button
        for sel in ["a:has-text('Continue')", "button:has-text('Continue')", "a.btn", "button.btn"]:
            try:
                if self.browser.page.locator(sel).first.is_visible():
                    self.browser.page.locator(sel).first.click()
                    break
            except Exception:
                pass

    # ---------- main actions ----------
    def fill_contact_required_only(self, *, email=None, phone=None, address=None):
        p = self.browser.page
        self.browser.goto(f"{BASE}/account/contact")
        if "/login" in p.url.lower():
            raise RuntimeError("Not logged in when opening /account/contact")

        fill_contact_fields(p, email=email or self.creds["email"], phone=phone, address=address)

        required_inputs = p.locator("input[required], textarea[required], select[required]")
        try:
            n = required_inputs.count()
        except Exception:
            n = 0

        for i in range(n):
            el = required_inputs.nth(i)
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            typ = (el.get_attribute("type") or "").lower()
            name = (el.get_attribute("name") or "").lower()
            idv  = (el.get_attribute("id") or "").lower()

            try:
                val = el.input_value()
                if val and len(val.strip()) > 0:
                    continue
            except Exception:
                pass

            try:
                if tag == "textarea":
                    el.fill("Reach us for details. We respond quickly.")
                elif typ == "email":
                    el.fill(email or self.creds["email"])
                elif typ in ("tel", "number"):
                    el.fill(phone or "9999999999")
                elif "zip" in (name + idv):
                    el.fill("560001")
                elif "city" in (name + idv):
                    el.fill("Bengaluru")
                elif "state" in (name + idv):
                    el.fill("Karnataka")
                elif "country" in (name + idv):
                    if tag == "select":
                        opts = el.locator("option[value]:not([value=''])")
                        if opts.count() > 0:
                            el.select_option(value=opts.nth(0).get_attribute("value"))
                        else:
                            el.select_option(index=0)
                    else:
                        el.fill("India")
                elif tag == "select":
                    opts = el.locator("option[value]:not([value=''])")
                    if opts.count() > 0:
                        el.select_option(value=opts.nth(0).get_attribute("value"))
                    else:
                        el.select_option(index=0)
                else:
                    el.fill("N/A")
            except Exception:
                continue

        for sel in ["button[type=submit]", "input[type=submit]", "button:has-text('Save')", "button:has-text('Update')"]:
            try:
                if p.locator(sel).first.is_visible():
                    with p.expect_navigation(wait_until="load"):
                        p.locator(sel).first.click()
                    break
            except Exception:
                pass

        self.log.log(self.name, "contact", "filled_required", "ok")

    def fill_listing_resume(self, *, website: str):
        p = self.browser.page
        self.browser.goto(f"{BASE}/account/resume")
        if "/login" in p.url.lower():
            raise RuntimeError("Not logged in when opening /account/resume")

        website_selectors = [
            "input[name='website']",
            "input[name='url']",
            "input#website",
            "input[placeholder*='website' i]",
            "input[placeholder*='http' i]",
        ]
        filled = False
        for sel in website_selectors:
            try:
                if p.is_visible(sel):
                    p.fill(sel, website)
                    filled = True
                    break
            except Exception:
                continue

        try:
            req_textareas = p.locator("textarea[required]")
            if req_textareas.count() > 0 and req_textareas.first.is_visible():
                req_textareas.first.fill("Learn more at our site for full details, offers, and support.")
        except Exception:
            pass

        for sel in ["button[type=submit]", "input[type=submit]", "button:has-text('Save')", "button:has-text('Update')", "button:has-text('Submit')"]:
            try:
                if p.locator(sel).first.is_visible():
                    with p.expect_navigation(wait_until="load"):
                        p.locator(sel).first.click()
                    break
            except Exception:
                pass

        self.log.log(self.name, "listing", "resume_saved", f"website_set={filled}")

    def go_dashboard_and_open_listing(self) -> str:
        p = self.browser.page
        self.browser.goto(f"{BASE}/")

        pop_btn = p.locator("#popover.toggle-member-info, #popover").first
        if pop_btn.is_visible():
            try:
                pop_btn.click()
            except Exception:
                try:
                    pop_btn.focus(); pop_btn.press("Enter")
                except Exception:
                    pass
        else:
            alt = p.locator("a.toggle-member-info[data-toggle='popover']").first
            if alt.is_visible():
                alt.click()

        try:
            p.wait_for_selector(".popover, [role='tooltip']", state="visible", timeout=5000)
        except Exception:
            try:
                p.evaluate("""() => {
                    const el = document.querySelector('#popover');
                    if (el && typeof jQuery !== 'undefined' && jQuery(el).popover) {
                        jQuery(el).popover('show');
                    }
                }""")
                p.wait_for_selector(".popover, [role='tooltip']", state="visible", timeout=3000)
            except Exception:
                pass

        public_href = None
        try:
            pop_scope = p.locator(".popover, [role='tooltip']").first
            anchors = pop_scope.locator("a[href]")
            count = anchors.count()
            for i in range(min(20, count)):
                href = anchors.nth(i).get_attribute("href") or ""
                if not href:
                    continue
                if href.startswith("/") or href.startswith(BASE):
                    full = href if href.startswith("http") else (BASE.rstrip("/") + href)
                    if all(x not in full for x in ["/login", "/account", "/checkout"]):
                        public_href = full
                        break
        except Exception:
            public_href = None

        if not public_href:
            anchors = p.locator("a[href*='//www.yplocal.com/'], a[href^='/']")
            try:
                count = anchors.count()
            except Exception:
                count = 0
            for i in range(min(50, count)):
                href = anchors.nth(i).get_attribute("href") or ""
                if not href:
                    continue
                full = href if href.startswith("http") else (BASE.rstrip("/") + href)
                if all(x not in full for x in ["/login", "/account", "/checkout"]):
                    public_href = full
                    break

        if not public_href:
            guesses = [
                "/profile", "/profiles", "/listing", "/listings",
                f"/profile/{self.creds['username']}",
            ]
            for g in guesses:
                try:
                    resp = p.goto(BASE.rstrip("/") + g, wait_until="load")
                    if resp and resp.ok and all(x not in p.url for x in ["/login", "/account"]):
                        public_href = p.url
                        break
                except Exception:
                    continue

        if not public_href:
            raise RuntimeError("Could not locate a public listing link from the Welcome popover or page.")

        if p.url != public_href:
            with p.expect_navigation(wait_until="load"):
                p.goto(public_href)

        self.log.log(self.name, "listing", "opened_public", p.url)
        return p.url

    # ---------- helpers ----------
    def _try_detect_logged_in(self) -> bool:
        p = self.browser.page
        try:
            self.browser.goto(f"{BASE}/account/contact")
            if "/login" in p.url.lower():
                return False
            return p.is_visible("form input, form textarea, form select")
        except Exception:
            return False

    def _dispatch_events(self, selector: str):
        p = self.browser.page
        try:
            p.evaluate("""(sel) => {
                const el = document.querySelector(sel);
                if (!el) return;
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                el.blur && el.blur();
            }""", selector)
        except Exception:
            pass

    def _register_flow(self):
        """
        Registration form needs email x2 & password x2.
        Captcha appears after fields are filled → prompt user to solve, then submit.
        """
        p = self.browser.page
        if "/checkout/3" not in p.url:
            self.browser.goto(f"{BASE}/checkout/3")

        # 1) Username / First / Last (best effort)
        base_fields = [
            ("input[name='username'], #username", self.creds["username"]),
            ("input[name='first_name'], #first_name", "Test"),
            ("input[name='last_name'], #last_name", "User"),
        ]
        for sel, val in base_fields:
            try:
                if p.is_visible(sel):
                    p.fill(sel, ""); p.fill(sel, str(val)); self._dispatch_events(sel)
            except Exception:
                pass

        # 2) Email ×2 — fill ALL visible email-like inputs with the same value
        try:
            email_inputs = p.locator("input[type='email'], input[name*='email' i], input[id*='email' i]")
            ecount = email_inputs.count()
        except Exception:
            ecount = 0
        for i in range(ecount):
            sel = None
            try:
                node = email_inputs.nth(i)
                if not node.is_visible(): continue
                _id = node.get_attribute("id"); _name = node.get_attribute("name")
                if _id: sel = f"#{_id}"
                elif _name: sel = f"input[name='{_name}']"
            except Exception:
                sel = None
            try:
                if sel:
                    p.fill(sel, ""); p.fill(sel, self.creds["email"]); self._dispatch_events(sel)
                else:
                    node.fill(self.creds["email"]); node.blur()
            except Exception:
                continue

        # 3) Password ×2 — fill ALL visible password-like inputs with the same value
        try:
            pw_inputs = p.locator("input[type='password'], input[name*='password' i], input[id*='password' i]")
            pcount = pw_inputs.count()
        except Exception:
            pcount = 0
        last_pw_selector = None
        for i in range(pcount):
            sel = None
            try:
                node = pw_inputs.nth(i)
                if not node.is_visible(): continue
                _id = node.get_attribute("id"); _name = node.get_attribute("name")
                if _id: sel = f"#{_id}"
                elif _name: sel = f"input[name='{_name}']"
            except Exception:
                sel = None
            try:
                if sel:
                    p.fill(sel, ""); p.fill(sel, self.creds["password"]); self._dispatch_events(sel); last_pw_selector = sel
                else:
                    node.fill(self.creds["password"]); node.blur()
            except Exception:
                continue

        # 4) ALWAYS prompt to solve CAPTCHA (since it appears after filling)
        self.browser.wait_manual_captcha(
            "\nSolve the CAPTCHA on the registration form (if shown), then press Enter here to submit…"
        )

        # 5) Submit
        submitted = False
        for sel in [
            "button[type=submit]", "input[type=submit]",
            "button:has-text('Create')", "button:has-text('Join')",
            "button:has-text('Sign Up')", "button:has-text('Register')",
            "button:has-text('Continue')"
        ]:
            try:
                if p.locator(sel).first.is_visible():
                    with p.expect_navigation(wait_until="load"):
                        p.locator(sel).first.click()
                    submitted = True
                    break
            except Exception:
                continue
        if not submitted and last_pw_selector:
            try:
                with p.expect_navigation(wait_until="load"):
                    p.press(last_pw_selector, "Enter")
                submitted = True
            except Exception:
                pass

        self.log.log(self.name, "register", "submitted", self.creds["username"])
        # Verification mail will be sent; outer flow calls verify_email() next.
