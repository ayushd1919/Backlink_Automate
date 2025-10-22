from playwright.sync_api import sync_playwright

class Browser:
    def __init__(self, headed: bool = False):
        self._p = sync_playwright().start()
        self.browser = self._p.chromium.launch(headless=not headed)
        self.ctx = self.browser.new_context()
        self.page = self.ctx.new_page()

    def goto(self, url: str):
        self.page.goto(url, wait_until="load")

    def wait_manual_captcha(self, msg="Solve CAPTCHA in the visible browser, then press Enter here..."):
        input(msg)

    def close(self):
        self.ctx.close()
        self.browser.close()
        self._p.stop()
