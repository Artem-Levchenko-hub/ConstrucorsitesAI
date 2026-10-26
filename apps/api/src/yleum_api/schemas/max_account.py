from pydantic import BaseModel


class MaxAccessPublic(BaseModel):
    """Admission to MAX projects: phone proof and current legal acceptance."""

    authenticated: bool = True
    email_verified: bool
    email_delivery_configured: bool
    phone_verified: bool
    phone_verification_required: bool
    phone_verification_available: bool
    registration_verification_reason: str | None = None
    can_create_project: bool
    reason: str | None = None
    legal_document_version: str
    payments_configured: bool
