from sites.base import BaseSite
from core.utils import rand_username, rand_password

class SiteTarget(BaseSite):
    name = "target"

    def __init__(self, headed=False):
        super().__init__(headed=headed)
        self.creds = {"username": rand_username("bk"), "password": rand_password(), "email": None}

    def register(self, email:str):
        self.creds["email"] = email
        # TODO: replace with your register page URL from HTML
        self.browser.goto("https://example.com/register")
        # TODO: fill username/email/password using your selectors
        # self.browser.page.fill("#username", self.creds["username"])
        # self.browser.page.fill("#email", email)
        # self.browser.page.fill("#password", self.creds["password"])
        # Manual CAPTCHA
        self.browser.wait_manual_captcha()
        # TODO: self.browser.page.click("button[type=submit]")

    def verify_email(self):
        link = self.mail.wait_for_verification_link(subject_hint="")
        self.browser.goto(link)

    def login(self):
        # If already logged in after verify, skip; else go to login and submit creds
        # TODO: implement with real selectors
        pass

    def update_profile(self, website:str) -> str:
        # TODO: navigate to profile edit, set website, save, open public profile
        # return detected public profile URL
        return "https://example.com/profile/bk_demo"
