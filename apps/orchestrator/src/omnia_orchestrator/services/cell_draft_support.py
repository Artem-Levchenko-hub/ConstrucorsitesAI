"""Deep helpers kept outside the Project Cell provider foundation boundary."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from omnia_orchestrator.services.provisioner import _template_source_dir


def trusted_template_source(template: str) -> Path:
    """Resolve a repository-owned template for initial cell seeding."""
    return _template_source_dir(template)


def signed_preview_session_url(
    preview_url: str,
    path: str,
    *,
    expires: int,
    signature: str,
) -> str:
    """Build the same-origin signed bootstrap URL without exposing query assembly."""
    return f"{preview_url}{path}?{urlencode({'expires': expires, 'signature': signature})}"
