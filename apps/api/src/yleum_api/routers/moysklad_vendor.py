"""MoySklad Vendor API: signed lifecycle, one-time owner pairing, revocation."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import jwt
from fastapi import APIRouter, Header, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from yleum_api.core.config import get_settings
from yleum_api.core.crypto import encrypt_strong
from yleum_api.core.deps import CurrentUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.models.app_integration import AccountIntegration, ProjectIntegrationBinding
from yleum_api.models.moysklad import MoyskladInstallation, MoyskladVendorReceipt
from yleum_api.routers import app_integrations
from yleum_api.services.entitlements import assert_integrations_allowed
from yleum_api.services.integration_credentials import load_credentials
from yleum_api.services.max_access import require_max_studio_access

router = APIRouter(tags=["moysklad-vendor"])
RESOURCE = "https://api.moysklad.ru/api/remap/1.2"
VENDOR_BASE = "https://apps-api.moysklad.ru/api/vendor/1.0"


def _config() -> tuple[UUID, str, str]:
    settings = get_settings()
    try:
        app_id = UUID(settings.integration_moysklad_app_id or "")
    except ValueError as exc:
        raise ApiError("moysklad_unavailable", "МойСклад ещё не настроен", 503) from exc
    uid = settings.integration_moysklad_app_uid or ""
    key = settings.integration_moysklad_secret_key
    if not uid or key is None or not key.get_secret_value():
        raise ApiError("moysklad_unavailable", "МойСклад ещё не настроен", 503)
    return app_id, uid, key.get_secret_value()


def _verify_jwt(authorization: str) -> tuple[str, datetime]:
    _, _, key = _config()
    if not authorization.startswith("Bearer ") or len(authorization) > 4096:
        raise ApiError("moysklad_unauthorized", "Неверная подпись МойСклад", 401)
    try:
        claims = jwt.decode(
            authorization[7:],
            key,
            algorithms=["HS256"],
            options={"require": ["iat", "exp", "jti"]},
            leeway=30,
        )
        issued, expiry = int(claims["iat"]), int(claims["exp"])
        jti = claims["jti"]
        now = int(time.time())
        if (
            issued > now + 30
            or expiry <= now
            or expiry - issued > 300
            or not isinstance(jti, str)
            or not 8 <= len(jti) <= 128
        ):
            raise ValueError("invalid lifetime")
    except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
        raise ApiError("moysklad_unauthorized", "Неверная подпись МойСклад", 401) from exc
    return hashlib.sha256(jti.encode()).hexdigest(), datetime.fromtimestamp(expiry, UTC)


def _outbound_jwt() -> str:
    _, uid, key = _config()
    now = int(time.time())
    return jwt.encode(
        {"sub": uid, "iat": now, "exp": now + 120, "jti": secrets.token_urlsafe(24)},
        key,
        algorithm="HS256",
    )


async def _body(request: Request) -> tuple[bytes, dict[str, object]]:
    raw = await request.body()
    if len(raw) > 16_384:
        raise ApiError("moysklad_request_invalid", "Некорректный запрос МойСклад", 400)
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ApiError("moysklad_request_invalid", "Некорректный запрос МойСклад", 400) from exc
    if not isinstance(body, dict):
        raise ApiError("moysklad_request_invalid", "Некорректный запрос МойСклад", 400)
    return raw, body


def _identity(app_id: UUID, body: dict[str, object]) -> str:
    expected_id, uid, _ = _config()
    if app_id != expected_id or body.get("appUid") != uid:
        raise ApiError("moysklad_app_invalid", "Другое решение МойСклад", 403)
    name = body.get("accountName")
    if not isinstance(name, str) or not 1 <= len(name) <= 200:
        raise ApiError("moysklad_request_invalid", "Некорректный запрос МойСклад", 400)
    return name


def _access_token(body: dict[str, object]) -> str | None:
    entries = body.get("access")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and entry.get("resource") in {RESOURCE, f"{RESOURCE}/"}:
                token = entry.get("access_token")
                if isinstance(token, str) and 8 <= len(token) <= 4096:
                    return token
    return None


def _deactivate_installation(installation: MoyskladInstallation, cause: str) -> None:
    """A suspension revokes access temporarily; an uninstall also forgets the owner."""
    installation.state = "uninstalled" if cause == "Uninstall" else "suspended"
    installation.token_enc = None
    if cause == "Uninstall":
        installation.user_id = None
    installation.pairing_code_hash = None
    installation.pairing_expires_at = None
    installation.vendor_activated = False


@router.put("/api/moysklad/vendor/1.0/apps/{app_id}/{account_id}")
@router.delete("/api/moysklad/vendor/1.0/apps/{app_id}/{account_id}")
async def vendor_lifecycle(
    app_id: UUID,
    account_id: UUID,
    request: Request,
    session: SessionDep,
    authorization: str = Header(default=""),
) -> dict[str, str]:
    jti_hash, expires_at = _verify_jwt(authorization)
    raw, body = await _body(request)
    name = _identity(app_id, body)
    digest = hashlib.sha256(
        request.method.encode() + b"\n" + request.url.path.encode() + b"\n" + raw
    ).hexdigest()
    prior = await session.get(MoyskladVendorReceipt, jti_hash)
    if prior is not None:
        if prior.request_hash != digest:
            raise ApiError("moysklad_replay", "Повторный запрос отклонён", 401)
        return prior.response
    installation = (
        await session.execute(
            select(MoyskladInstallation)
            .where(MoyskladInstallation.account_id == account_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if installation is not None and installation.app_id != app_id:
        raise ApiError("moysklad_app_invalid", "Другое решение МойСклад", 403)
    if request.method == "PUT":
        cause = body.get("cause")
        if cause not in {"Install", "Resume", "TariffChanged", "Autoprolongation"}:
            raise ApiError("moysklad_request_invalid", "Некорректный запрос МойСклад", 400)
        token = _access_token(body)
        if cause in {"Install", "Resume"} and token is None:
            raise ApiError("moysklad_request_invalid", "Токен МойСклад отсутствует", 400)
        if installation is None:
            if token is None:
                raise ApiError("moysklad_request_invalid", "Установка не найдена", 400)
            installation = MoyskladInstallation(
                account_id=account_id,
                app_id=app_id,
                account_name=name,
                token_enc=None,
                state="installed",
            )
            session.add(installation)
        installation.account_name = name
        installation.state = "installed"
        if token is not None:
            installation.token_enc = encrypt_strong(json.dumps({"token": token}))
            if installation.user_id is not None:
                connection = await app_integrations._account_connection(
                    session, installation.user_id, "moysklad"
                )
                if (
                    connection is not None
                    and connection.auth_mode == "connector"
                    and connection.public_config.get("moysklad_account_id") == str(account_id)
                ):
                    connection.credentials_enc = installation.token_enc
                    connection.status = "active"
                    connection.last_error = None
                    bindings = (
                        await session.scalars(
                            select(ProjectIntegrationBinding).where(
                                ProjectIntegrationBinding.integration_id == connection.id,
                                ProjectIntegrationBinding.provider == "moysklad",
                                ProjectIntegrationBinding.enabled.is_(True),
                            )
                        )
                    ).all()
                    for binding in bindings:
                        if binding.status == "error":
                            binding.status = "ready"
                            binding.last_error = None
        result = {"status": "Activated" if installation.user_id else "SettingsRequired"}
    else:
        if body.get("cause") not in {"Uninstall", "Suspend"}:
            raise ApiError("moysklad_request_invalid", "Некорректный запрос МойСклад", 400)
        if installation is not None:
            linked_user = installation.user_id
            _deactivate_installation(installation, str(body["cause"]))
            if linked_user is not None:
                connection = await app_integrations._account_connection(
                    session, linked_user, "moysklad"
                )
                if (
                    connection is not None
                    and connection.auth_mode == "connector"
                    and connection.public_config.get("moysklad_account_id") == str(account_id)
                ):
                    connection.status = "error"
                    connection.credentials_enc = encrypt_strong("{}")
                    connection.last_error = "Решение отключено в МойСклад"
        result = {}
    session.add(
        MoyskladVendorReceipt(
            jti_hash=jti_hash, request_hash=digest, response=result, expires_at=expires_at
        )
    )
    await session.commit()
    return result


@router.get("/api/moysklad/vendor/1.0/apps/{app_id}/{account_id}")
async def vendor_status(
    app_id: UUID, account_id: UUID, session: SessionDep, authorization: str = Header(default="")
) -> dict[str, str]:
    _verify_jwt(authorization)
    if app_id != _config()[0]:
        raise ApiError("moysklad_app_invalid", "Другое решение МойСклад", 403)
    installation = await session.get(MoyskladInstallation, account_id)
    if installation is None or installation.state != "installed":
        raise ApiError("moysklad_not_found", "Установка не найдена", 404)
    return {"status": "Activated" if installation.user_id else "SettingsRequired"}


class ContextRequest(BaseModel):
    token: str = Field(min_length=8, max_length=256)


@router.post("/api/integrations/moysklad/context")
async def pairing_code(payload: ContextRequest, session: SessionDep) -> dict[str, str]:
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            response = await client.post(
                f"{VENDOR_BASE}/context/user",
                json={"token": payload.token},
                headers={
                    "Authorization": f"Bearer {_outbound_jwt()}",
                    "Accept-Encoding": "gzip",
                },
            )
    except httpx.RequestError as exc:
        raise ApiError("moysklad_unavailable", "МойСклад временно недоступен", 503) from exc
    if response.status_code != 200:
        raise ApiError("moysklad_context_invalid", "Не удалось подтвердить аккаунт МойСклад", 401)
    try:
        context = response.json()
        account_id = UUID(context["accountId"])
    except (ValueError, TypeError, KeyError) as exc:
        raise ApiError("moysklad_context_invalid", "Некорректный ответ МойСклад", 502) from exc
    if context.get("role") != "admin":
        raise ApiError(
            "moysklad_admin_required", "Подключить склад может только администратор", 403
        )
    installation = (
        await session.execute(
            select(MoyskladInstallation)
            .where(MoyskladInstallation.account_id == account_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if installation is None or installation.state != "installed" or not installation.token_enc:
        raise ApiError("moysklad_not_found", "Сначала установите решение в МойСклад", 409)
    code = secrets.token_urlsafe(18)
    installation.pairing_code_hash = hashlib.sha256(code.encode()).hexdigest()
    installation.pairing_expires_at = datetime.now(UTC) + timedelta(minutes=10)
    await session.commit()
    return {"code": code, "account_name": installation.account_name}


@router.get("/api/integrations/moysklad/setup", response_class=HTMLResponse)
async def setup() -> HTMLResponse:
    nonce = secrets.token_urlsafe(18)
    page = """<!doctype html>
<html lang=ru><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Подключить МойСклад к Yleum</title>
<style>
body{font:16px system-ui;margin:2rem;max-width:38rem;color:#172034}
p{line-height:1.5}code{font-size:1.3rem;overflow-wrap:anywhere}
</style>
<h1>Подключить МойСклад к Yleum</h1>
<p id=status>Подтверждаем аккаунт…</p>
<div id=result hidden>
<p>Скопируйте код и вставьте в Yleum: Интеграции → МойСклад. Код действует 10 минут.</p>
<code id=code></code>
<p><a href="https://yleum.ru" target=_blank rel="noopener noreferrer">Открыть Yleum</a></p>
</div>
<script nonce="NONCE">
(()=>{
  const id=Math.floor(Math.random()*2147483647);
  const s=document.getElementById('status');
  let attempts=0;
  const request=()=>{
    if(attempts++>=10){
      s.textContent='МойСклад не ответил. Переоткройте решение.';
      clearInterval(retry);
      return;
    }
    window.parent.postMessage({name:'UserContextRequest',messageId:id},
                              'https://online.moysklad.ru');
  };
  const h=async e=>{
    if(e.origin!=='https://online.moysklad.ru'||e.source!==window.parent)return;
    if(e.data?.name!=='UserContextResponse'||e.data.correlationId!==id)return;
    clearInterval(retry);
    window.removeEventListener('message',h);
    try{
      const r=await fetch('/api/integrations/moysklad/context',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({token:e.data.token}),credentials:'omit'
      });
      const v=await r.json();
      if(!r.ok)throw Error();
      document.getElementById('code').textContent=v.code;
      document.getElementById('result').hidden=false;
      s.textContent='Аккаунт подтверждён.';
    }catch{s.textContent='Не удалось подтвердить аккаунт. Переоткройте решение.'}
  };
  window.addEventListener('message',h);
  const retry=setInterval(request,1000);
  request();
})();
</script></html>""".replace("NONCE", nonce)
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'nonce-{nonce}'; "
                "style-src 'unsafe-inline'; connect-src 'self'; "
                "frame-ancestors https://online.moysklad.ru; "
                "base-uri 'none'; form-action 'none'"
            ),
        },
    )


class ClaimRequest(BaseModel):
    code: str = Field(min_length=20, max_length=64)


class WarehouseSettings(BaseModel):
    organization_id: UUID
    store_id: UUID


async def _warehouse_connection(
    session: SessionDep, project_id: UUID, user_id: UUID
) -> AccountIntegration:
    await app_integrations._owned_max_project(session, project_id, user_id)
    connection = await app_integrations._account_connection(session, user_id, "moysklad")
    if connection is None or connection.status != "active":
        raise ApiError("integration_not_found", "Сначала подключите МойСклад", 409)
    return connection


async def _warehouse_options(client: httpx.AsyncClient, kind: str) -> list[dict[str, str]]:
    try:
        response = await client.get(f"{RESOURCE}/entity/{kind}", params={"limit": 1000})
        if response.status_code >= 300:
            raise ApiError("integration_provider_unavailable", "МойСклад не вернул настройки", 503)
        payload = response.json()
        rows = payload["rows"]
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValueError("invalid rows")
        options = [{"id": str(UUID(row["id"])), "name": str(row["name"])[:200]} for row in rows]
        if any(not option["name"] for option in options):
            raise ValueError("invalid name")
        return options
    except (httpx.RequestError, ValueError, TypeError, KeyError) as exc:
        raise ApiError(
            "integration_provider_unavailable", "МойСклад не вернул настройки", 503
        ) from exc


@router.get("/api/projects/{project_id}/app-integrations/moysklad/options")
async def warehouse_options(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> dict[str, list[dict[str, str]]]:
    connection = await _warehouse_connection(session, project_id, current_user.id)
    credentials = await load_credentials(session, connection)
    token = credentials.get("token")
    if not token:
        raise ApiError("integration_configuration_invalid", "Переподключите МойСклад", 409)
    async with httpx.AsyncClient(
        timeout=12, headers={"Authorization": f"Bearer {token}"}, follow_redirects=False
    ) as client:
        organizations = await _warehouse_options(client, "organization")
        stores = await _warehouse_options(client, "store")
    return {"organizations": organizations, "stores": stores}


@router.put("/api/projects/{project_id}/app-integrations/moysklad/settings")
async def save_warehouse_settings(
    project_id: UUID,
    payload: WarehouseSettings,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, str]:
    connection = await _warehouse_connection(session, project_id, current_user.id)
    options = await warehouse_options(project_id, session, current_user)
    if str(payload.organization_id) not in {item["id"] for item in options["organizations"]} or str(
        payload.store_id
    ) not in {item["id"] for item in options["stores"]}:
        raise ApiError(
            "integration_configuration_invalid", "Выберите организацию и склад из списка", 422
        )
    connection.public_config = {
        **connection.public_config,
        "organization_id": str(payload.organization_id),
        "store_id": str(payload.store_id),
    }
    await session.commit()
    return {"status": "saved"}


async def _activate_vendor(app_id: UUID, account_id: UUID) -> bool:
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            response = await client.put(
                f"{VENDOR_BASE}/apps/{app_id}/{account_id}/status",
                json={"status": "Activated"},
                headers={
                    "Authorization": f"Bearer {_outbound_jwt()}",
                    "Accept-Encoding": "gzip",
                },
            )
        return response.status_code == 200
    except httpx.RequestError:
        return False


@router.post("/api/projects/{project_id}/app-integrations/moysklad/claim")
async def claim(
    project_id: UUID, payload: ClaimRequest, session: SessionDep, current_user: CurrentUserDep
) -> dict[str, str]:
    await app_integrations._owned_max_project(session, project_id, current_user.id)
    require_max_studio_access(current_user)
    await assert_integrations_allowed(session, current_user.id)
    digest = hashlib.sha256(payload.code.encode()).hexdigest()
    installation = (
        await session.execute(
            select(MoyskladInstallation)
            .where(MoyskladInstallation.pairing_code_hash == digest)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        installation is None
        or installation.pairing_expires_at is None
        or installation.pairing_expires_at <= datetime.now(UTC)
        or installation.state != "installed"
        or not installation.token_enc
    ):
        raise ApiError(
            "moysklad_pairing_invalid", "Код истёк. Откройте решение в МойСклад снова", 400
        )
    if installation.user_id not in {None, current_user.id}:
        raise ApiError("moysklad_already_linked", "Этот склад уже связан с другим аккаунтом", 409)
    connection = await app_integrations._account_connection(session, current_user.id, "moysklad")
    if connection is not None and (
        connection.auth_mode != "connector"
        or connection.public_config.get("moysklad_account_id") != str(installation.account_id)
    ):
        raise ApiError(
            "moysklad_connection_conflict", "Сначала отключите прежний склад в Yleum", 409
        )
    if connection is None:
        connection = AccountIntegration(
            user_id=current_user.id,
            created_by_user_id=current_user.id,
            provider="moysklad",
            credentials_enc=installation.token_enc,
        )
        session.add(connection)
        await session.flush()
    connection.auth_mode = "connector"
    connection.credentials_enc = installation.token_enc
    connection.public_config = {
        **(connection.public_config or {}),
        "moysklad_account_id": str(installation.account_id),
    }
    connection.capabilities = ["catalog", "inventory", "orders"]
    connection.account_label = installation.account_name
    connection.status = "active"
    connection.last_error = None
    connection.verified_at = datetime.now(UTC)
    connection.last_checked_at = connection.verified_at
    installation.user_id = current_user.id
    await app_integrations._bind(session, project_id, connection)
    await session.commit()
    if await _activate_vendor(installation.app_id, installation.account_id):
        installation.vendor_activated = True
        installation.pairing_code_hash = None
        installation.pairing_expires_at = None
        await session.commit()
        return {"status": "connected"}
    return {"status": "vendor_sync_pending"}
