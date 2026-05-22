"""GitHub integration: OAuth App connect/callback + connection status.

The OAuth round-trip is stateless — the user id rides in a short-lived signed
`state` token (CSRF protection) rather than server-side session storage. The
access token is encrypted before it touches the database.
"""

from typing import Annotated
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Query, status
from fastapi.responses import RedirectResponse

from omnia_api.core.config import get_settings
from omnia_api.core.crypto import encrypt_token
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.security import create_oauth_state, verify_oauth_state
from omnia_api.models.github_connection import GithubConnection
from omnia_api.schemas.github import GithubConnectResponse, GithubStatus
from omnia_api.services import github_client

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/integrations/github", tags=["github"])


def _web_redirect(outcome: str) -> RedirectResponse:
    """Bounce the browser back to the web app's account page after OAuth.

    Derives the web origin from the first CORS origin (in prod that's the web
    app's URL), avoiding a dedicated setting another session is toggling.
    """
    origins = get_settings().cors_origins_list
    base = (origins[0] if origins else "http://localhost:3000").rstrip("/")
    return RedirectResponse(f"{base}/account?github={outcome}", status_code=status.HTTP_302_FOUND)


@router.get("/connect", response_model=GithubConnectResponse)
async def connect(current_user: CurrentUserDep) -> GithubConnectResponse:
    settings = get_settings()
    # Fail fast if we can't complete the round-trip — don't send the user to
    # GitHub only to choke on the callback.
    if settings.github_oauth_client_id is None or settings.github_token_enc_key is None:
        raise ApiError(
            "github_unavailable",
            "GitHub integration is not configured on the server.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    params = {
        "client_id": settings.github_oauth_client_id,
        "redirect_uri": settings.github_oauth_redirect_uri,
        "scope": settings.github_oauth_scopes,
        "state": create_oauth_state(current_user.id),
        "allow_signup": "false",
    }
    authorize_url = (
        f"{settings.github_oauth_base.rstrip('/')}/login/oauth/authorize?{urlencode(params)}"
    )
    return GithubConnectResponse(authorize_url=authorize_url)


@router.get("/callback")
async def callback(
    session: SessionDep,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    if error or not code or not state:
        return _web_redirect("denied")
    user_id = verify_oauth_state(state)
    if user_id is None:
        return _web_redirect("error")

    try:
        token, scopes = await github_client.exchange_code_for_token(code)
        gh_user = await github_client.get_authenticated_user(token)
    except ApiError:
        log.warning("github.callback.exchange_failed", user_id=str(user_id))
        return _web_redirect("error")

    username = str(gh_user.get("login") or "")
    if not username:
        return _web_redirect("error")

    conn = await session.get(GithubConnection, user_id)
    encrypted = encrypt_token(token)
    if conn is None:
        session.add(
            GithubConnection(
                user_id=user_id,
                access_token_encrypted=encrypted,
                github_username=username,
                scopes=scopes or None,
            )
        )
    else:
        conn.access_token_encrypted = encrypted
        conn.github_username = username
        conn.scopes = scopes or None
    await session.commit()
    return _web_redirect("connected")


@router.get("/status", response_model=GithubStatus)
async def github_status(current_user: CurrentUserDep, session: SessionDep) -> GithubStatus:
    conn = await session.get(GithubConnection, current_user.id)
    if conn is None:
        return GithubStatus(connected=False)
    return GithubStatus(
        connected=True,
        github_username=conn.github_username,
        scopes=conn.scopes,
        connected_at=conn.connected_at,
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(current_user: CurrentUserDep, session: SessionDep) -> None:
    conn = await session.get(GithubConnection, current_user.id)
    if conn is not None:
        await session.delete(conn)
        await session.commit()
