from pydantic import BaseModel

class SiteResult(BaseModel):
    registered: bool = False
    verified: bool = False
    logged_in: bool = False
    profile_updated: bool = False
    profile_url: str | None = None
    reason: str | None = None
