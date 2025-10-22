from sites.base import BaseSite
from core.utils import rand_username, rand_password
from core.formfill import fill_contact_fields, select2_set_value
from playwright.sync_api import TimeoutError as PWTimeout
from urllib.parse import urlparse
import random, string

BASE = "https://a2zsocialnews.com"

def _rand_token(n=6):
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))

def _default_title(website_url: str) -> str:
    host = urlparse(website_url).netloc.replace("www.", "") or "our site"
    return f"Latest update from {host} – {_rand_token()}"

def _default_description(website_url: str) -> str:
    txt = (
        f"This short news relates to {website_url}. We are sharing useful information for readers. "
        f"This text is intentionally lengthened to satisfy minimum length rules. "
        f"It covers key points, benefits, and brief context for the audience. More updates will follow. "
    )
    if len(txt) < 260:
        txt += ("continuing the details. " * 12)
    return txt

class AlreadyExistsError(Exception): ...
class  WrongPasswordError(Exception): ...

class SiteA2ZSocialNews(BaseSite):
    name = "a2zsocialnews"
    requires_email_verification = False

    def __init__(self, headed=False):
        super().__init__(headed=headed)
        self.creds = {"username": None, "password": None, "email": None}
        self._logged_in = False

    # ---------- public api ----------
    def set_creds(self, account_email: str, username: str | None, password: str | None):
        self.creds["email"] = account_email
        self.creds["username"] = username or rand_username("a2z")
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
                new_pw = input("\nEnter the NEW password you just set (saved for future runs): ").strip()
                if not new_pw:
                    raise RuntimeError("No new password provided.")
                self.creds["password"] = new_pw
                self.login(self.creds["email"], self.creds["password"])

    def login(self, user: str, password: str):
        p = self.browser.page
        self.browser.goto(f"{BASE}/login/")
        p.wait_for_selector("#user_login")
        p.fill("#user_login", str(user))
        p.fill("#user_pass", str(password))
        p.press("#user_pass", "Enter")
        self.log.log(self.name, "login", "attempt", str(user))

        if self._wait_for_login_landing():
            self._logged_in = True
            return
        body = (p.text_content("body") or "").lower()
        if any(s in body for s in ["incorrect password", "invalid password", "password you entered"]):
            raise WrongPasswordError("Wrong password")
        raise RuntimeError("Login did not succeed and no explicit error was detected.")

    # ---------- submit news ----------
    def submit_news(self, *, target_website: str, title: str | None, description: str | None,
                    category_value: str = "1", location_value: str | None = "102",
                    email: str | None = None, phone: str | None = None, address: str | None = None) -> str:
        """
        Fill /submit-news/ (handles Select2 + tagsinput), manual reCAPTCHA, submit (handles preview),
        then open My News → the *Approved* card matching our title and return its public URL.
        """
        p = self.browser.page
        self.browser.goto(f"{BASE}/submit-news/")

        def wait(sel): p.wait_for_selector(sel, state="visible", timeout=15000)

        # 1) Website
        wait("#articleUrl"); p.fill("#articleUrl", target_website)

        # 2) Title (ensure reasonable length)
        news_title = title or _default_title(target_website)
        if len(news_title) < 25:
            news_title += " – quality update for readers"
        wait("#submitpro_title"); p.fill("#submitpro_title", news_title)

        # 3) Category (Select2)
        wait("#submitpro_category"); select2_set_value(p, "#submitpro_category", category_value)

        # 4) Tags (bootstrap-tagsinput)
        try:
            if p.is_visible(".bootstrap-tagsinput input"):
                for t in ["news", "update", "info"]:
                    p.click(".bootstrap-tagsinput input")
                    p.type(".bootstrap-tagsinput input", t)
                    p.keyboard.press("Enter")
            else:
                p.fill("#tagsinput", "news, update, info")
        except Exception:
            pass

        # 5) Location (optional; Select2)
        if location_value:
            try:
                if p.is_visible("#submitpro_location"):
                    select2_set_value(p, "#submitpro_location", location_value)
            except Exception:
                pass

        # 6) Contact fields shared helper (email/phone/address)
        filled = fill_contact_fields(
            p,
            email=email or self.creds["email"],
            phone=phone,
            address=address
        )
        self.log.log(self.name, "form", "contacts",
                     f"email={filled['email']} phone={filled['phone']} address={filled['address']}")

        # 7) Description (>=250 chars)
        news_desc = description or _default_description(target_website)
        if len(news_desc) < 250:
            news_desc += " " + ("continuing the details. " * 12)
        wait("#submitpro_desc"); p.fill("#submitpro_desc", news_desc)

        # 8) Agree
        if p.is_visible("#agree-checkbox"): p.check("#agree-checkbox")

        # 9) Manual reCAPTCHA
        self.browser.wait_manual_captcha(
            "\nSolve reCAPTCHA on the Submit News page. When it shows a green tick, press Enter to submit…"
        )

        # 10) Submit (some themes show preview; click again if still present)
        btn = "input[type=submit][value='Preview & Submit']"
        wait(btn)
        with p.expect_navigation(wait_until="load"):
            p.click(btn)
        try:
            if p.locator(btn).first.is_visible():
                with p.expect_navigation(wait_until="load"):
                    p.click(btn)
        except Exception:
            pass

        # 11) My News → click the *Approved* post matching our title (public page link)
        # "Approved Posts" list shows .blog-box with .poststatus 'Approved' and the title link in h3.entry-title a
        # We find the card that (a) is Approved and (b) has our exact title, then click its title link. :contentReference[oaicite:1]{index=1}
        self.browser.goto(f"{BASE}/my-news/")
        # Prefer exact match within an Approved card
        approved_card = p.locator(
            ".blog-box",
            has=p.locator(".poststatus:has-text('Approved')")
        ).filter(
            has=p.locator(f"h3.entry-title a:has-text('{news_title}')")
        ).first

        if approved_card.count() == 0:
            # Fallback: first Approved card (sometimes themes tweak title text)
            approved_card = p.locator(".blog-box", has=p.locator(".poststatus:has-text('Approved')")).first

        post_link = approved_card.locator("h3.entry-title a").first
        with p.expect_navigation(wait_until="load", timeout=15000):
            post_link.click()

        self.log.log(self.name, "news", "published", p.url)
        return p.url

    # ---------- internals ----------
    def _register_flow(self):
        p = self.browser.page
        self.browser.goto(f"{BASE}/register/")
        try: p.fill("#user_login", self.creds["username"])
        except Exception: pass
        p.fill("#user_email", self.creds["email"])
        try: p.fill("#user_password", self.creds["password"])
        except Exception: pass
        try: p.fill("#user_cpassword", self.creds["password"])
        except Exception: pass
        try: p.fill("#nickname", self.creds["username"])
        except Exception: pass

        self.browser.wait_manual_captcha(
            "\nSolve the reCAPTCHA on the Register page, then press Enter here to submit..."
        )
        with p.expect_navigation(wait_until="load"):
            p.click("input[type=submit][name=submit]")
        self.log.log(self.name, "register", "submitted", self.creds["username"])

        body = (p.text_content("body") or "").lower()
        if "/register/" in p.url and any(k in body for k in [
            "already registered", "already exists",
            "email address is already", "username already",
        ]):
            raise AlreadyExistsError("Account already exists")

    def _try_detect_logged_in(self) -> bool:
        p = self.browser.page
        try:
            self.browser.goto(f"{BASE}/submit-news/")
            return p.is_visible("#submitpro_title") or p.is_visible("#articleUrl")
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
        self.browser.goto(f"{BASE}/wp-login.php?action=lostpassword")
        if p.is_visible("#user_login"):
            try: p.fill("#user_login", str(user_or_email))
            except Exception: pass
        self.log.log(self.name, "password_reset", "started", str(user_or_email))
        self.browser.wait_manual_captcha(
            "\nPASSWORD RESET: In the browser, click 'Get New Password' (or similar), "
            "complete the reset via the email you receive, then press Enter here to continue…"
        )
