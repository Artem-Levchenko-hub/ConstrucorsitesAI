from pydantic import BaseModel


class MaxAccessPublic(BaseModel):
    """Admission to new MAX projects: email proof and current legal acceptance."""

    authenticated: bool = True
    email_verified: bool
    email_delivery_configured: bool
    can_create_project: bool
    reason: str | None = None
    legal_document_version: str
    payments_configured: bool
