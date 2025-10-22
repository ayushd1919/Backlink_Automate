from core.browser import Browser
from core.mail import MailClient
from core.logger import CsvLogger

class BaseSite:
    name = "base"

    def __init__(self, headed=False):
        self.browser = Browser(headed=headed)
        self.mail = MailClient()
        self.log = CsvLogger()

    # To be implemented in subclass
    def register(self, email:str): ...
    def verify_email(self): ...
    def login(self): ...
    def update_profile(self, website:str) -> str: ...

    def close(self):
        self.browser.close()
