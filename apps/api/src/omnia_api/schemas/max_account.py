from pydantic import BaseModel


class MaxAccessPublic(BaseModel):
    """What a MAX Studio account needs before building: a verified email, nothing else."""

    authenticated: bool = True
    email_verified: bool
    email_delivery_configured: bool
    can_create_project: bool
    reason: str | None = None
    legal_document_version: str
    payments_configured: bool
