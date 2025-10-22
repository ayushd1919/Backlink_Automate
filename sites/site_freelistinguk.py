# sites/site_freelistinguk.py
from sites.base import BaseSite
from core.utils import rand_password
from core.mail import MailUnavailable
from playwright.sync_api import TimeoutError as PWTimeout

import os, json

DIR = "https://www.freelistinguk.com"
ART_DIR = os.path.join("artifacts", "freelistinguk.com")

class VerificationTimeout(Exception): ...
class WrongPassword(Exception): ...
class AccountNotFound(Exception): ...
class AlreadyExists(Exception): ...

class SiteFreeListingUK(BaseSite):
    """
    Test-friendly flow with artifacts + robust form scoping:

      - On start: try LOGIN first using saved creds from artifacts/freelistinguk.com/<email>.json
      - If account not found → /register → verify email (IMAP/POP3 or manual paste) → login
      - If register says "already exists" → jump back to login (using saved creds)
      - Create listing → open newest public /listings/... → return URL
    """
    name = "freelistinguk.com"
    requires_email_verification = True

    def __init__(self, headed: bool = False):
        super().__init__(headed=headed)
        self.creds = {"email": None, "username": None, "password": None}
        self._logged_in = False

    # ---------- Artifacts helpers ----------
    def _artifact_path(self, email: str) -> str:
        safe = (email or "unknown").replace("/", "_").replace("\\", "_")
        return os.path.join(ART_DIR, f"{safe}.json")

    def _load_saved_creds(self, email: str) -> dict | None:
        try:
            path = self._artifact_path(email)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("account_email") == email and data.get("password"):
                    return data
        except Exception:
            pass
        return None

    def _save_creds(self):
        try:
            os.makedirs(ART_DIR, exist_ok=True)
            snap = self.get_creds_snapshot()
            with open(self._artifact_path(self.creds["email"]), "w", encoding="utf-8") as f:
                json.dump(snap, f, indent=2)
            self.log.log(self.name, "artifacts", "saved_creds", self._artifact_path(self.creds["email"]))
        except Exception as e:
            self.log.log(self.name, "artifacts", "save_failed", str(e))

    # ---------- Public API ----------
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
        return {
            "account_email": self.creds["email"],
            "username": self.creds["username"],
            "password": self.creds["password"],
        }

    def register_or_login_with_verification(self):
        if self._try_detect_logged_in():
            self._logged_in = True
            self._save_creds()
            self.log.log(self.name, "session", "already_logged_in", self.creds["email"])
            return

        # 1) LOGIN FIRST (uses saved creds if we had them)
        try:
            self.login(self.creds["email"], self.creds["password"])
            self._save_creds()
            return
        except AccountNotFound:
            # 2) REGISTER if account missing
            self._register_direct()
            self.verify_email()
            self.login(self.creds["email"], self.creds["password"])
            self._save_creds()
            return
        except WrongPassword:
            # We'll try register path—if "already exists", we fall back to login again.
            pass
        except Exception:
            pass

        # 3) REGISTER (handles already-exists → back to login)
        try:
            self._register_direct()
            self.verify_email()
        except AlreadyExists:
            pass

        # 4) LOGIN
        self.login(self.creds["email"], self.creds["password"])
        self._save_creds()

    # ---------- Auth ----------
    def login(self, email: str, password: str):
        p = self.browser.page
        self.browser.goto(f"{DIR}/login")
        p.wait_for_load_state("domcontentloaded")

        # Find the REAL login form (not header search)
        form = self._find_login_form()
        if not form:
            raise RuntimeError("Could not locate login form on /login.")

        # Identifier: email preferred; else username/text
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

        self.log.log(self.name, "login", "attempt", f"{email} (user={self.creds['username']})")

        # Submit THIS form
        submitted = False
        try:
            btn = form.locator("button[type='submit'], input[type='submit']").first
            if btn and btn.is_visible():
                with p.expect_navigation(wait_until="load"):
                    btn.click()
                submitted = True
        except Exception:
            submitted = False

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

        # Outcomes
        if self._try_detect_logged_in():
            self._logged_in = True
            self.log.log(self.name, "login", "success", email)
            return

        body = (p.text_content("body") or "").lower()
        if any(k in body for k in [
            "invalid email address", "invalid username", "email address not found",
            "you don't have an account", "no account", "no user found", "register"
        ]):
            raise AccountNotFound("Account not found; registration required.")
        if any(k in body for k in ["incorrect password", "invalid password", "wrong password", "password you entered"]):
            raise WrongPassword("Password incorrect.")
        raise RuntimeError("Login failed: no success indicators, no clear error message.")

    # Form finder for /login
    def _find_login_form(self):
        p = self.browser.page
        try:
            # Form that has a password field (strong signal)
            f = p.locator("form").filter(has=p.locator("input[type='password']")).first
            if f and f.is_visible():
                return f
        except Exception:
            pass
        try:
            # Or: form that has email/username + password
            f = p.locator("form").filter(has=p.locator("input[type='password']")).filter(
                has=p.locator("input[type='email'], input[name='email'], input[name='user_email'], input[name='username'], input[name='log'], input[type='text']")
            ).first
            if f and f.is_visible():
                return f
        except Exception:
            pass
        try:
            # As a last resort: :has() CSS
            f = p.locator(
                "form:has(input[type='password']):has(input[type='email'], input[name='email'], input[name='user_email'], input[name='username'], input[name='log'], input[type='text'])"
            ).first
            if f and f.is_visible():
                return f
        except Exception:
            pass
        return None

    # ---------- Registration ----------
    def _register_direct(self):
        p = self.browser.page
        self.browser.goto(f"{DIR}/register")
        p.wait_for_load_state("domcontentloaded")

        reg_form = self._find_registration_form()
        if not reg_form:
            raise RuntimeError("Could not locate the registration form on /register.")

        email = self.creds["email"]
        username = self.creds["username"]
        pw = self.creds["password"]

        # Fill within the scoped form only
        self._fill_first_visible(reg_form,
            ["#name", "input[name='name']", "input[placeholder*='name' i]"],
            self._friendly_name_from_email(email)
        )
        self._fill_first_visible(reg_form,
            ["#user_login", "input[name='user_login']", "input[placeholder*='username' i]"],
            username
        )
        self._fill_first_visible(reg_form,
            ["#user_email", "input[name='user_email']", "input[type='email']", "input[name='email']"],
            email
        )

        # Passwords
        pw_inputs = reg_form.locator("input[type='password']")
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
            self._fill_first_visible(reg_form, ["#pass1", "input[name='pass1']", "input[id*='pass1' i]"], pw)
            self._fill_first_visible(reg_form, ["#pass2", "input[name='pass2']", "input[id*='pass2' i]"], pw)
            try:
                cnt = pw_inputs.count()
                if cnt == 1: pw_inputs.first.fill(pw)
            except Exception:
                pass

        # Manual CAPTCHA pause if present
        try:
            self.browser.wait_manual_captcha(
                "\nIf a CAPTCHA is visible on the Register page, solve it now, then press Enter here to submit…"
            )
        except Exception:
            pass

        # Submit THIS form
        submitted = False
        try:
            btn = reg_form.locator(
                "button#register, input#register, button[type='submit'], input[type='submit'], button:has-text('Register'), input[value*='Register' i]"
            ).first
            if btn and btn.is_visible():
                with p.expect_navigation(wait_until="load"):
                    btn.click()
                submitted = True
        except Exception:
            submitted = False

        if not submitted:
            try:
                last_pw = reg_form.locator("input[type='password']").last
                if last_pw and last_pw.is_visible():
                    with p.expect_navigation(wait_until="load"):
                        last_pw.press("Enter")
                    submitted = True
            except Exception:
                pass

        if not submitted:
            raise RuntimeError("Could not submit the registration form (no visible submit inside the form).")

        # Detect "already exists" after submit
        body = (p.text_content("body") or "").lower()
        if any(k in body for k in [
            "already registered", "already exists", "email address is already",
            "username already", "user already exists"
        ]):
            self.log.log(self.name, "register", "exists", f"{username}/{email}")
            raise AlreadyExists("Account/username already exists")

        self.log.log(self.name, "register", "submitted", email)

    # Find registration form (not header search)
    def _find_registration_form(self):
        p = self.browser.page
        try:
            reg = p.locator("form").filter(has=p.locator("#user_email")).first
            if reg and reg.count() > 0 and reg.is_visible(): return reg
        except Exception: pass
        try:
            reg = p.locator("form").filter(has=p.locator("#user_login")).filter(has=p.locator("input[type='password']")).first
            if reg and reg.count() > 0 and reg.is_visible(): return reg
        except Exception: pass
        try:
            reg = p.locator("form:has(#user_email), form:has(#user_login):has(input[type='password'])").first
            if reg and reg.is_visible(): return reg
        except Exception: pass
        return None

    # ---------- Email verification ----------
    def verify_email(self):
        """
        Fetch verification link via IMAP/POP3 (Spam included by mail.py), or prompt for manual paste.
        After opening the link, if the site still needs login, we login automatically.
        """
        link = None
        try:
            link = self.mail.wait_for_verification_link(
                subject_hint="FreeListingUK",
                timeout_sec=600,
                prefer_domain="freelistinguk.com",
            )
        except MailUnavailable as e:
            self.log.log(self.name, "email", "mail_unavailable", str(e))
            link = None

        if not link:
            print("\n[Mail] Could not fetch verification link automatically.")
            url = input("Paste the FreeListingUK verification URL here and press Enter: ").strip()
            if not url:
                raise VerificationTimeout("No verification link provided and mail fetch unavailable.")
            link = url

        self.log.log(self.name, "email", "verification_link", link)
        self.browser.goto(link)
        self.browser.page.wait_for_load_state("domcontentloaded")

        # If verify page still requires login, do it now
        if not self._try_detect_logged_in():
            try:
                self.login(self.creds["email"], self.creds["password"])
                self._save_creds()
            except Exception:
                pass  # if already logged in by the verify action, ignore

    # ---------- Listing ----------
    def create_listing_and_get_public_url(self, *, website:str, title:str,
                                          addr_line:str, country_uk:str, city:str,
                                          description:str, tags:list[str] | None = None,
                                          choose_n_categories:int = 1) -> str:
        p = self.browser.page
        self.browser.goto(f"{DIR}/create-listing")
        p.wait_for_selector("form#create-listing", timeout=20000)

        p.fill("input[name='listing_title']", title)
        p.fill("#listing-address", addr_line)
        p.fill("#listing-state", country_uk); p.wait_for_timeout(300)
        p.fill("#listing-city", city); p.wait_for_timeout(300)

        if len(description) < 200:
            description = (description + " ") * 20
            description = description[:220]
        p.fill("textarea[name='listing_content']", description)

        cats = p.locator("#multi-categories-checkboxes input[type='checkbox']")
        try:
            total = cats.count()
        except Exception:
            total = 0
        want = max(1, min(choose_n_categories, total or 1))
        for i in range(want):
            try:
                c = cats.nth(i)
                if not c.is_checked(): c.check()
            except Exception:
                continue

        if tags:
            try: p.fill("#tags", ", ".join(tags[:8]))
            except Exception: pass

        if p.is_visible("input[name='agree_terms']"):
            p.check("input[name='agree_terms']")

        with p.expect_navigation(wait_until="load"):
            p.click("#submit")

        if "/listings/" not in (p.url or ""):
            self.browser.goto(f"{DIR}/my-listings")
            p.wait_for_load_state("domcontentloaded")
            link = p.locator("a[href*='/listings/']").first
            href = link.get_attribute("href") if link and link.is_visible() else None
            if href:
                with p.expect_navigation(wait_until="load"):
                    p.goto(href)

        if "/listings/" in (p.url or ""):
            self.log.log(self.name, "listing", "published", p.url)
            return p.url
        raise RuntimeError("Listing created, but public URL not found from My Listings.")

    # ---------- Helpers ----------
    def _fill_first_visible(self, scope, selectors: list[str], value: str):
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

    def _friendly_name_from_email(self, email: str) -> str:
        base = email.split("@")[0].replace(".", " ").replace("_", " ").replace("-", " ")
        base = base.strip().title() or "User"
        return base[:40]

    def _try_detect_logged_in(self) -> bool:
        p = self.browser.page
        try:
            self.browser.goto(f"{DIR}/dashboard")
            return (
                p.is_visible("a[href='/dashboard']") or
                p.is_visible("a[href='/my-listings']") or
                p.is_visible("a[href='/create-listing']")
            )
        except Exception:
            return False
