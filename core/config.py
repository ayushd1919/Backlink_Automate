import json, os
from copy import deepcopy

DEFAULT_PATH = os.path.join("config", "settings.json")

def load_config(path: str | None = None) -> dict:
    path = path or DEFAULT_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if "defaults" not in cfg:
        cfg["defaults"] = {}
    if "sites" not in cfg:
        cfg["sites"] = {}
    return cfg

def for_site(cfg: dict, domain: str) -> dict:
    """
    Merge defaults + site overrides into one dict for a given domain.
    """
    base = deepcopy(cfg.get("defaults", {}))
    site = cfg.get("sites", {}).get(domain, {})
    base.update(site or {})
    return base
