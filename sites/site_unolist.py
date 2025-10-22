# sites/site_unolist.py
from sites.base import BaseSite

class SiteUnolist(BaseSite):
    name = "unolist"

    def __init__(self, headed=False):
        super().__init__(headed=headed)
        self.creds = {"username": None, "password": None, "email": None}

    # ——— Phase 1: Register/Login/Profile ———
    # Fill these once you share the register/login/profile HTMLs.
    def register(self, email: str):
        self.creds["email"] = email
        # Registration page exists at /login/login.html (from site header).
        # TODO (needs HTML of the register form): fill selectors & submit.

    def verify_email(self):
        # TODO (needs sample verification email HTML): parse link & open.
        pass

    def login(self):
        # TODO (needs login form HTML): open /login/login.html, submit creds.
        pass

    def update_profile(self, website: str) -> str:
        # TODO (needs profile edit + public profile HTML): set website & return profile URL.
        return ""

    # ——— Phase 0 (already works): Validate a public listing has our website link ———
    def validate_listing_link(self, listing_url: str) -> str:
        """
        Open a Unolist /desc/* page and return the Website href shown on the page.
        """
        self.browser.goto(listing_url)
        # The page shows: <td>Website :</td> <td><a href="...">...</a></td>
        # Try robust locators (first: text neighbor; fallback: first visible link in the details table).
        page = self.browser.page
        try:
            a = page.locator("xpath=//td[contains(., 'Website')]/following-sibling::td[1]//a").first
            page.wait_for_timeout(300)  # tiny settle
            href = a.get_attribute("href")
            if not href:
                raise ValueError("Website link not found")
            return href
        except Exception:
            # Fallback: first detail anchor under the details section
            a = page.locator("css=table a[href^='http']").first
            href = a.get_attribute("href")
            if not href:
                raise ValueError("Website link not found (fallback)")
            return href
