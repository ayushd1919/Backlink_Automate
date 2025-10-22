from playwright.sync_api import Page

# Common selector candidates seen across many WP/SubmitPro themes & directories
EMAIL_SELECTORS   = ["#submitpro_email", "input[name='email']", "#email", "input[type='email']"]
PHONE_SELECTORS   = ["#submitpro_phone", "input[name='phone']", "#phone", "input[type='tel']", "input[name='contact_phone']"]
ADDRESS_SELECTORS = ["#submitpro_address", "input[name='address']", "#address", "textarea[name='address']", "textarea#address", "input[name='street_address']"]

def _fill_if_visible(page: Page, selector: str, value: str) -> bool:
    try:
        if page.is_visible(selector):
            page.fill(selector, value)
            # fire 'input' + 'change' for React/Select2/vanilla listeners
            try:
                page.evaluate("""(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }""", selector)
            except Exception:
                pass
            return True
    except Exception:
        pass
    return False

def fill_contact_fields(page: Page, *, email: str | None, phone: str | None, address: str | None) -> dict:
    """
    Try a list of known selectors for email/phone/address and fill whichever exist.
    Returns a dict of which fields were filled to aid logging.
    """
    result = {"email": False, "phone": False, "address": False}

    if email:
        for sel in EMAIL_SELECTORS:
            if _fill_if_visible(page, sel, email):
                result["email"] = True
                break

    if phone:
        for sel in PHONE_SELECTORS:
            if _fill_if_visible(page, sel, phone):
                result["phone"] = True
                break

    if address:
        for sel in ADDRESS_SELECTORS:
            if _fill_if_visible(page, sel, address):
                result["address"] = True
                break

    return result

def select2_set_value(page: Page, select_css: str, value: str) -> None:
    """
    Robustly set a Select2-enhanced <select> element's value and dispatch 'change'.
    """
    try:
        page.select_option(select_css, value=value)
        page.evaluate("""(sel)=>{const el=document.querySelector(sel); if(el){ el.dispatchEvent(new Event('change',{bubbles:true})); }}""", select_css)
    except Exception:
        # Last-resort: hard set via JS
        page.evaluate("""(sel,val)=>{const el=document.querySelector(sel); if(el){ el.value=val; el.dispatchEvent(new Event('change',{bubbles:true})); }}""", select_css, value)
