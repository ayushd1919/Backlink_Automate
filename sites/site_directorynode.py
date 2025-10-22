from sites.base import BaseSite
from core.utils import rand_username, rand_password
from core.formfill import fill_contact_fields, select2_set_value
from playwright.sync_api import TimeoutError as PWTimeout

DIR = "https://directorynode.com"

class AlreadyExistsError(Exception): pass
class WrongPasswordError(Exception): pass

class SiteDirectoryNode(BaseSite):
    name = "directorynode"
    requires_email_verification = False

    def __init__(self, headed=False):
        super().__init__(headed=headed)
        self.creds = {"username": None, "password": None, "email": None}
        self._logged_in = False

    # ---------- Public API ----------
    def set_creds(self, account_email: str, username: str | None, password: str | None):
        self.creds["email"] = account_email
        self.creds["username"] = username or rand_username("dn")
        self.creds["password"] = password or rand_password(12)

    def get_creds_snapshot(self) -> dict:
        return {"account_email": self.creds["email"], "username": self.creds["username"], "password": self.creds["password"]}

    def register_or_login_with_recovery(self):
        if self._try_detect_logged_in():
            self._logged_in = True
            self.log.log(self.name, "session", "already_logged_in", self.creds["username"])
            return
        try:
            self._register_flow()
            if not self._try_detect_logged_in():
                self.login(self.creds["email"], self.creds["password"])
        except AlreadyExistsError:
            self.log.log(self.name, "register", "exists", self.creds["username"])
            try:
                self.login(self.creds["email"], self.creds["password"])
            except WrongPasswordError:
                self._recover_password_guided(self.creds["email"])
                new_pw = input("\nEnter the NEW password you just set (will be saved for future runs): ").strip()
                if not new_pw: raise RuntimeError("No new password entered; cannot proceed.")
                self.creds["password"] = new_pw
                self.login(self.creds["email"], self.creds["password"])

    def login(self, user: str, password: str):
        p = self.browser.page
        self.browser.goto(f"{DIR}/login/")
        p.wait_for_selector("#user_login")
        p.fill("#user_login", str(user))
        p.fill("#user_pass", str(password))
        if p.is_visible("#rememberme"):
            try: p.check("#rememberme")
            except Exception: pass
        p.press("#user_pass", "Enter")
        self.log.log(self.name, "login", "attempt", str(user))

        if self._wait_for_login_landing():
            self._logged_in = True
            return
        body = (p.text_content("body") or "").lower()
        if any(s in body for s in ["incorrect password", "password you entered for the username", "invalid password"]):
            raise WrongPasswordError("Wrong password")
        raise RuntimeError("Login did not succeed and no explicit error was detected.")

    def add_directory(self, website:str, *, title:str, category_value:str,
                      location_value:str|None=None, tags:list[str]|None=None,
                      email:str|None=None, phone:str|None=None, address:str|None=None,
                      description:str="") -> str:
        """
        Submit /submit-directory/ and return the NEW post URL.
        After submit, we go to /my-directories/, find the *Approved* card for this post,
        click its title, and return the public URL. If the site logs us out, we auto re-login.
        """
        p = self.browser.page
        self.browser.goto(f"{DIR}/submit-directory/")

        # Website
        p.fill("#articleUrl", website)

        # Title
        if len(title) < 30:
            title = (title + " – " + "Quality services and information").strip()
        p.fill("#submitpro_title", title)
        expected_slug = self._slugify(title)

        # Category (Select2-aware)
        select2_set_value(p, "#submitpro_category", category_value)

        # Tags
        try:
            if p.is_visible(".bootstrap-tagsinput input"):
                for t in (tags or ["best","top","trusted"])[:3]:
                    p.click(".bootstrap-tagsinput input")
                    p.type(".bootstrap-tagsinput input", t)
                    p.keyboard.press("Enter")
            else:
                p.fill("#tagsinput", ", ".join((tags or ["best","top","trusted"])[:3]))
        except Exception:
            pass

        # Location (optional)
        if location_value:
            try:
                if p.is_visible("#submitpro_location"):
                    select2_set_value(p, "#submitpro_location", location_value)
            except Exception:
                pass

        # Contact fields (reusable helper)
        filled = fill_contact_fields(p, email=email or self.creds["email"], phone=phone, address=address)
        self.log.log(self.name, "form", "contacts", f"email={filled['email']} phone={filled['phone']} address={filled['address']}")

        # Description (>=250)
        if len(description) < 250:
            pad = (" " + ("Our directory entry highlights offerings, service scope, team, pricing, "
                           "coverage areas, and support information to assist visitors in making "
                           "well-informed decisions aligned with their needs.")) * 3
            description = (description + pad)[:260]
        p.fill("#submitpro_desc", description)

        # Terms
        if p.is_visible("#agree-checkbox"):
            p.check("#agree-checkbox")

        # CAPTCHA → Submit
        self.browser.wait_manual_captcha("\nSolve reCAPTCHA on the Submit Directory page. When it turns green, press Enter to submit…")
        with p.expect_navigation(wait_until="load"):
            p.click("input.btn.btn-primary[type=submit]")

        # If we landed directly on the post:
        if ("/submit-directory" not in p.url) and ("/my-directories" not in p.url) and ("/my-account" not in p.url) and ("/login" not in p.url):
            self.log.log(self.name, "directory", "published", p.url)
            return p.url

        # Continue via My Directories -> Approved post
        return self._open_approved_directory_post(expected_slug=expected_slug, fallback_title=title)

    # ---------- internals ----------
    def _register_flow(self):
        p = self.browser.page
        self.browser.goto(f"{DIR}/register/")
        p.fill("#user_login", self.creds["username"])
        p.fill("#user_email", self.creds["email"])
        p.fill("#user_password", self.creds["password"])
        p.fill("#user_cpassword", self.creds["password"])
        p.fill("#nickname", self.creds["username"])

        self.browser.wait_manual_captcha("\nSolve the reCAPTCHA on the Register page, then press Enter here to submit...")
        with p.expect_navigation(wait_until="load"):
            p.click("input[type=submit][name=submit]")
        self.log.log(self.name, "register", "submitted", self.creds["username"])

        body = (p.text_content("body") or "").lower()
        if "/register/" in p.url and any(k in body for k in [
            "already registered","already exists","email address is already","username already"
        ]):
            raise AlreadyExistsError("Account already exists")

    def _try_detect_logged_in(self) -> bool:
        p = self.browser.page
        try:
            self.browser.goto(f"{DIR}/my-account/")
            return p.is_visible("form#adduser") or p.is_visible("#nickname") or p.is_visible("#email")
        except Exception:
            return False

    def _wait_for_login_landing(self) -> bool:
        p = self.browser.page
        try:
            p.wait_for_timeout(800)
            return self._try_detect_logged_in()
        except Exception:
            return False

    def _recover_password_guided(self, user_or_email: str):
        p = self.browser.page
        self.browser.goto(f"{DIR}/wp-login.php?action=lostpassword")
        if p.is_visible("#user_login"):
            try: p.fill("#user_login", str(user_or_email))
            except Exception: pass
        self.log.log(self.name, "password_reset", "started", str(user_or_email))
        self.browser.wait_manual_captcha(
            "\nPASSWORD RESET: In the browser, click 'Get New Password' (or similar), "
            "complete the reset via the email you receive, then press Enter here to continue…"
        )

    def _slugify(self, s: str) -> str:
        import re, unicodedata
        s = unicodedata.normalize("NFKD", s).encode("ascii","ignore").decode("ascii")
        s = s.strip().lower()
        s = re.sub(r"[^a-z0-9\s-]", "", s)
        s = re.sub(r"\s+", "-", s)
        s = re.sub(r"-{2,}", "-", s)
        return s

    def _ensure_logged_in(self):
        """If we got bounced to /login, log back in and return to My Directories."""
        p = self.browser.page
        if "/login" in p.url or "logged_out=true" in p.url:
            self.log.log(self.name, "session", "relogin", p.url)
            self.login(self.creds["email"], self.creds["password"])

    def _open_approved_directory_post(self, expected_slug: str, fallback_title: str) -> str:
        """
        Go to /my-directories/, wait up to ~60s for an Approved card,
        click the exact-title Approved card if present, else the newest Approved,
        else fall back to any plausible public post link.
        Also handles login bounce by re-logging in.
        """
        p = self.browser.page

        # Always start at My Directories
        self.browser.goto(f"{DIR}/my-directories/")
        self._ensure_logged_in()
        if "/login" in p.url:
            # after relogin, go back
            self.browser.goto(f"{DIR}/my-directories/")

        # Poll up to 60s for an Approved card (some sites approve asynchronously)
        found = False
        for _ in range(30):  # 30 * 2s = 60s
            try:
                # Prefer exact title inside an Approved card
                card = p.locator(
                    ".blog-box",
                    has=p.locator(".poststatus:has-text('Approved')")
                ).filter(
                    has=p.locator(f"h3.entry-title a:has-text('{fallback_title}')")
                ).first
                if card.count() > 0 and card.is_visible():
                    href = card.locator("h3.entry-title a").first.get_attribute("href")
                    if href:
                        found = True
                        # click; if it logs us out, relogin and go to href
                        with p.expect_navigation(wait_until="load", timeout=15000):
                            card.locator("h3.entry-title a").first.click()
                        if "/login" in p.url:
                            self._ensure_logged_in()
                            self.browser.goto(href)
                        self.log.log(self.name, "directory", "published", p.url)
                        return p.url

                # Else any Approved card
                any_approved = p.locator(".blog-box", has=p.locator(".poststatus:has-text('Approved')")).first
                if any_approved.count() > 0 and any_approved.is_visible():
                    href = any_approved.locator("h3.entry-title a").first.get_attribute("href")
                    if href:
                        found = True
                        with p.expect_navigation(wait_until="load", timeout=15000):
                            any_approved.locator("h3.entry-title a").first.click()
                        if "/login" in p.url:
                            self._ensure_logged_in()
                            self.browser.goto(href)
                        self.log.log(self.name, "directory", "published", p.url)
                        return p.url
            except Exception:
                pass
            # wait 2s then retry
            try: p.wait_for_timeout(2000)
            except Exception: pass

        # If no Approved yet, fall back to slug scan over all anchors on My Directories
        anchors = p.locator("a[href*='://directorynode.com/']")
        best = None
        try:
            for i in range(anchors.count()):
                a = anchors.nth(i)
                href = a.get_attribute("href") or ""
                if not href: continue
                if any(x in href for x in ["/submit-directory", "/my-directories", "/my-account", "/login", "/register"]):
                    continue
                if expected_slug and expected_slug in href.lower():
                    best = href; break
                if not best: best = href
        except Exception:
            best = None

        if best:
            with p.expect_navigation(wait_until="load"):
                p.goto(best)
            if "/login" in p.url:
                self._ensure_logged_in()
                self.browser.goto(best)
            self.log.log(self.name, "directory", "published", p.url)
            return p.url

        # Last resort: if we're already on a public page (rare bounce)
        if all(s not in p.url for s in ["/submit-directory", "/my-directories", "/my-account", "/login"]):
            self.log.log(self.name, "directory", "published", p.url)
            return p.url

        raise RuntimeError("Could not find an Approved listing to open (scanned and retried).")
