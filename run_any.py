import argparse, json, os, sys
from urllib.parse import urlparse
from dotenv import load_dotenv

from core.config import load_config, for_site
from sites.site_directorynode import SiteDirectoryNode
from sites.site_a2zsocialnews import SiteA2ZSocialNews
from sites.site_yplocal import SiteYPLocal
from sites.site_freelistinguk import SiteFreeListingUK
from sites.site_bizidex import SiteBizidex

ARTIFACTS = "artifacts"

SITE_REGISTRY = {
    "directorynode.com": {
        "cls": SiteDirectoryNode,
        "default_action": "directory",
        "creds_file": "dirnode_creds.json",
    },
    "a2zsocialnews.com": {
        "cls": SiteA2ZSocialNews,
        "default_action": "news",
        "creds_file": "a2z_creds.json",
    },
    "yplocal.com": {
        "cls": SiteYPLocal,
        "default_action": "listing",
        "creds_file": "yplocal_creds.json",
    },
    "freelistinguk.com": {
        "cls": SiteFreeListingUK,
        "default_action": "create_listing",
        "creds_file": "freelistinguk_creds.json",
    },
    "www.freelistinguk.com": {
        "cls": SiteFreeListingUK,
        "default_action": "create_listing",
        "creds_file": "freelistinguk_creds.json",
    },
    "bizidex.com": {
        "cls": SiteBizidex,
        "default_action": "publish_listing",
        "creds_file": "bizidex_creds.json",
    },
    "www.bizidex.com": {
        "cls": SiteBizidex,
        "default_action": "publish_listing",
        "creds_file": "bizidex_creds.json",
    },
}

def _ensure_dir(p: str):
    if p:
        os.makedirs(p, exist_ok=True)

def _load_creds(path: str, email_fallback: str | None, fresh: bool):
    _ensure_dir(os.path.dirname(path))
    if (not fresh) and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if email_fallback and "account_email" not in data:
                data["account_email"] = email_fallback
            return data
    return {"account_email": email_fallback, "username": None, "password": None}

def _save_creds(path: str, creds: dict):
    _ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(creds, f, indent=2)

def main():
    load_dotenv()
    ap = argparse.ArgumentParser("Backlink Automator — Multi-site launcher")
    ap.add_argument("--target-site", required=True, help="Domain (e.g., directorynode.com, yplocal.com, freelistinguk.com, bizidex.com)")
    ap.add_argument("--website", required=True, help="Backlink URL to include in the submission (if applicable)")
    ap.add_argument("--config", default="config/settings.json", help="Path to project config JSON")
    ap.add_argument("--headed", action="store_true", help="Show browser (solve CAPTCHAs manually)")
    ap.add_argument("--fresh-creds", action="store_true", help="Ignore saved creds and register new")
    # Optional overrides
    ap.add_argument("--email", default=None, help="Override account email for this run (else use config)")
    ap.add_argument("--title", default=None, help="Override title if the site uses it")
    ap.add_argument("--desc", default=None, help="Override description if the site uses it")
    args = ap.parse_args()

    # normalize domain
    domain = urlparse("https://" + args.target_site).netloc.lower().replace("www.", "")
    domain_key = args.target_site.lower()
    if domain_key in SITE_REGISTRY:
        entry = SITE_REGISTRY[domain_key]
    elif domain in SITE_REGISTRY:
        entry = SITE_REGISTRY[domain]
    else:
        print(f"[ERR] No adapter registered for '{args.target_site}'.", file=sys.stderr)
        return 2

    SiteCls = entry["cls"]
    creds_path = os.path.join(ARTIFACTS, entry["creds_file"])

    # Load config and per-site values
    cfg = load_config(args.config)
    vals = for_site(cfg, domain)

    # Resolve inputs (CLI overrides > per-site config > default config)
    account_email = args.email or vals.get("account_email") or cfg.get("default", {}).get("account_email")
    if not account_email:
        print("[ERR] account_email missing (provide via --email or config).", file=sys.stderr)
        return 2
    contact_email = vals.get("contact_email") or account_email
    phone = vals.get("phone")
    address = vals.get("address")

    # Save/load creds per site
    creds = _load_creds(creds_path, account_email, args.fresh_creds)

    # Instantiate site adapter
    site = SiteCls(headed=args.headed)
    try:
        site.set_creds(
            account_email=account_email,
            username=creds.get("username"),
            password=creds.get("password"),
        )

        # ---- PHASE 1: Auth (register/login + email verification if needed) ----
        if hasattr(site, "register_or_login_with_recovery"):
            site.register_or_login_with_recovery()
        elif hasattr(site, "register_or_login_with_verification"):
            site.register_or_login_with_verification()
        else:
            raise RuntimeError(f"{site.__class__.__name__} has no register/login entrypoint")

        # Force a final login check so that PHASE 2 is always authenticated
        if hasattr(site, "ensure_logged_in"):
            site.ensure_logged_in()

        # Persist any updated creds
        final = site.get_creds_snapshot()
        _save_creds(creds_path, final)

        # ---- PHASE 2: Site-specific action (always after login) ----
        action = entry["default_action"]

        if action == "directory":  # DirectoryNode
            title = args.title or vals.get("title") or "Premium services and solutions – quality info and support"
            desc = args.desc or vals.get("desc") or ""
            category_value = vals.get("category_value") or "21"
            location_value = vals.get("location_value") or "102"
            tags = vals.get("tags") or ["best", "top", "trusted"]

            url = site.add_directory(
                website=args.website,
                title=title,
                category_value=category_value,
                location_value=location_value,
                tags=tags,
                email=contact_email,
                phone=phone,
                address=address,
                description=desc,
            )

        elif action == "news":  # A2Z Social News
            title = args.title or vals.get("title")
            desc  = args.desc  or vals.get("desc") or ""
            category_value = vals.get("category_value") or "1"
            location_value = vals.get("location_value") or "102"

            url = site.submit_news(
                target_website=args.website,
                title=title,
                description=desc,
                category_value=category_value,
                location_value=location_value,
                email=contact_email,
                phone=phone,
                address=address,
            )

        elif action == "listing":  # YPLocal
            site.fill_contact_required_only(
                email=contact_email,
                phone=phone,
                address=address,
            )
            site.fill_listing_resume(website=args.website)
            url = site.go_dashboard_and_open_listing()

        elif action == "create_listing":  # FreeListingUK
            title = args.title or vals.get("title") or "Quality Services – Trusted UK Provider"
            addr_line = vals.get("address_line") or vals.get("address") or "221B Baker Street"
            country_uk = vals.get("country_uk") or "United Kingdom"
            city = vals.get("city") or "London"
            desc = args.desc or vals.get("desc") or cfg.get("default", {}).get("desc") or \
                   "Professional services for customers across the UK. Contact us for details."
            tags = vals.get("tags") or cfg.get("default", {}).get("tags") or ["best", "top", "trusted"]

            url = site.create_listing_and_get_public_url(
                website=args.website,
                title=title,
                addr_line=addr_line,
                country_uk=country_uk,
                city=city,
                description=desc,
                tags=tags,
                choose_n_categories=1,
            )

        elif action == "publish_listing":  # Bizidex
            url = site.complete_profile_and_add_website_then_publish(
                website=args.website
            )

        else:
            raise RuntimeError(f"Unknown default_action '{action}' for {domain}")

        print(f"[OK] Public link: {url}")

    finally:
        try:
            site.close()
        except Exception:
            pass

    return 0

if __name__ == "__main__":
    sys.exit(main())
