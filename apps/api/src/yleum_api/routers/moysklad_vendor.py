"""MoySklad Vendor API: signed lifecycle, one-time owner pairing, revocation."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import jwt
from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from yleum_api.core.config import get_settings
from yleum_api.core.crypto import encrypt_strong
from yleum_api.core.deps import CurrentUserDep, OptionalUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.models.app_integration import AccountIntegration, ProjectIntegrationBinding
from yleum_api.models.moysklad import MoyskladInstallation, MoyskladVendorReceipt
from yleum_api.models.project import Project
from yleum_api.routers import app_integrations
from yleum_api.services.entitlements import assert_integrations_allowed
from yleum_api.services.integration_credentials import load_credentials
from yleum_api.services.max_access import require_max_studio_access

router = APIRouter(tags=["moysklad-vendor"])
RESOURCE = "https://api.moysklad.ru/api/remap/1.2"
VENDOR_BASE = "https://apps-api.moysklad.ru/api/vendor/1.0"

RETURN_COOKIE = "__Host-moysklad-return"
RETURN_PATH = "/api/integrations/moysklad/return"
RETURN_PURPOSE = "moysklad-native-return"
RETURN_TTL = 15 * 60


class PublicInstallInfo(BaseModel):
    available: bool
    install_url: str | None


class PublicInstallStart(BaseModel):
    install_url: str


def _return_signing_key() -> bytes:
    # Separate key and audience: routing cookies cannot become auth/vendor JWTs.
    return hmac.digest(
        get_settings().jwt_secret.get_secret_value().encode(),
        RETURN_PURPOSE.encode(),
        "sha256",
    )


def _return_intent(raw: str | None) -> tuple[UUID, UUID, int] | None:
    if not raw or len(raw) > 2048:
        return None
    try:
        claims = jwt.decode(
            raw,
            _return_signing_key(),
            algorithms=["HS256"],
            audience=RETURN_PURPOSE,
            options={
                "require": ["iat", "exp", "owner", "project", "ver", "aud"],
                "verify_exp": False,
                "verify_iat": False,
            },
        )
        issued, expiry, version = claims["iat"], claims["exp"], claims["ver"]
        if any(type(value) is not int for value in (issued, expiry, version)):
            return None
        now = int(time.time())
        if issued > now + 30 or expiry <= now or not 0 < expiry - issued <= RETURN_TTL:
            return None
        if version < 0:
            return None
        return UUID(claims["owner"]), UUID(claims["project"]), version
    except (jwt.PyJWTError, ValueError, TypeError, AttributeError):
        return None


def _navigation_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


@router.get(
    "/api/projects/{project_id}/app-integrations/moysklad/install", response_model=PublicInstallInfo
)
async def public_install_info(
    project_id: UUID, response: Response, session: SessionDep, current_user: CurrentUserDep
) -> PublicInstallInfo:
    await app_integrations._owned_max_project(session, project_id, current_user.id)
    require_max_studio_access(current_user)
    url = get_settings().integration_moysklad_public_install_url
    _navigation_headers(response)
    return PublicInstallInfo(available=bool(url), install_url=url)


@router.post(
    "/api/projects/{project_id}/app-integrations/moysklad/install/start",
    response_model=PublicInstallStart,
)
async def start_public_install(
    project_id: UUID, response: Response, session: SessionDep, current_user: CurrentUserDep
) -> PublicInstallStart:
    await app_integrations._owned_max_project(session, project_id, current_user.id)
    require_max_studio_access(current_user)
    await assert_integrations_allowed(session, current_user.id)
    url = get_settings().integration_moysklad_public_install_url
    if not url:
        raise ApiError(
            "moysklad_unavailable",
            "Публичная установка решения МойСклад пока недоступна",
            503,
        )
    now = int(time.time())
    intent = jwt.encode(
        {
            "owner": str(current_user.id),
            "project": str(project_id),
            "ver": current_user.session_version,
            "iat": now,
            "exp": now + RETURN_TTL,
            "aud": RETURN_PURPOSE,
        },
        _return_signing_key(),
        algorithm="HS256",
    )
    response.set_cookie(
        RETURN_COOKIE,
        intent,
        max_age=RETURN_TTL,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    _navigation_headers(response)
    return PublicInstallStart(install_url=url)


@router.get(RETURN_PATH)
async def native_return(
    request: Request, session: SessionDep, current_user: OptionalUserDep
) -> RedirectResponse:
    intent = _return_intent(request.cookies.get(RETURN_COOKIE))
    if intent is not None and current_user is None:
        response = RedirectResponse(
            "/login?next=%2Fapi%2Fintegrations%2Fmoysklad%2Freturn",
            status_code=303,
        )
        _navigation_headers(response)
        return response
    destination = "/max"
    if intent is not None and current_user is not None:
        owner, project, version = intent
        if owner == current_user.id and version == current_user.session_version:
            try:
                await app_integrations._owned_max_project(session, project, current_user.id)
                require_max_studio_access(current_user)
                await assert_integrations_allowed(session, current_user.id)
            except ApiError as exc:
                if exc.status_code not in {401, 403, 404, 409}:
                    raise
            else:
                destination = f"/max/{project}?panel=services&integration=moysklad"
    response = RedirectResponse(destination, status_code=303)
    response.delete_cookie(RETURN_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
    _navigation_headers(response)
    return response


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


def _iframe_page(page: str, *, frame_ancestors: str) -> HTMLResponse:
    nonce = secrets.token_urlsafe(18)
    return HTMLResponse(
        page.replace("SCRIPT_NONCE", nonce),
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'nonce-{nonce}'; "
                "style-src 'unsafe-inline'; connect-src 'self'; "
                f"frame-ancestors {frame_ancestors}; base-uri 'none'; form-action 'none'"
            ),
        },
    )


def _bridge_session_marker(owner: UUID, version: int) -> str:
    return hmac.digest(
        _return_signing_key(), f"native-connect:{owner}:{version}".encode(), "sha256"
    ).hex()


@router.get("/api/integrations/moysklad/connect/session")
async def native_connect_session(
    response: Response, current_user: CurrentUserDep
) -> dict[str, str]:
    require_max_studio_access(current_user)
    _navigation_headers(response)
    return {"session_marker": _bridge_session_marker(current_user.id, current_user.session_version)}


@router.get("/api/integrations/moysklad/connect", response_model=None)
async def native_connect(session: SessionDep, current_user: OptionalUserDep) -> Response:
    if current_user is None:
        response = RedirectResponse(
            "/login?next=%2Fapi%2Fintegrations%2Fmoysklad%2Fconnect", status_code=303
        )
        _navigation_headers(response)
        return response
    require_max_studio_access(current_user)
    rows = (
        await session.execute(
            select(Project.id, Project.name, Project.owner_id)
            .where(Project.owner_id == current_user.id, Project.template == "max_miniapp")
            .order_by(Project.created_at.desc(), Project.id)
            .limit(100)
        )
    ).all()
    projects = [
        {"id": str(row.id), "name": row.name[:200]}
        for row in rows
        if row.owner_id == current_user.id
    ]
    email = current_user.email or ""
    local, _, domain = email.partition("@")
    label = f"{local[:1]}***@{domain}" if domain else "Аккаунт Yleum"
    bootstrap = json.dumps(
        {
            "nonce": secrets.token_urlsafe(24),
            "projects": projects,
            "account_label": label[:254],
            "session_marker": _bridge_session_marker(current_user.id, current_user.session_version),
        },
        ensure_ascii=True,
    ).replace("<", "\\u003c")
    page = """<!doctype html><html lang=ru><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Вход в Yleum для МойСклад</title>
<style>body{font:16px system-ui;margin:2rem;max-width:38rem}p{line-height:1.5}</style>
<h1>Подключение Yleum</h1>
<p id="status">Выберите миниапп и настройте склад в окне МойСклад.
Не закрывайте это окно до сохранения настроек.</p>
<script nonce="SCRIPT_NONCE">
(()=>{
 const bridge=BOOTSTRAP;
 const origin=window.location.origin;
 const opener=window.opener;
 const s=document.getElementById('status');
 const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
 const offered=new Set(bridge.projects.map(p=>p.id));
 const seen=new Set();let busy=false,cancelled=false,claimStarted=false,bound=null,options=null;
 const reply=(id,status,extra={})=>{
  if(opener)opener.postMessage({name:'MoyBridgeResult',nonce:bridge.nonce,
   request_id:id,status,...extra},origin);
 };
 setTimeout(()=>{cancelled=true;s.textContent='Срок окна входа истёк. Переоткройте решение.';
  if(opener)opener.postMessage({name:'MoyBridgeExpired',nonce:bridge.nonce},origin);},600000);
 const validOptions=v=>v&&['organizations','stores'].every(key=>Array.isArray(v[key])&&
  v[key].length<=1000&&v[key].every(x=>x&&typeof x.id==='string'&&uuid.test(x.id)&&
   typeof x.name==='string'&&x.name.length<=200));
 window.addEventListener('message',async e=>{
  if(e.origin!==origin||e.source!==opener||!e.data||e.data.nonce!==bridge.nonce)return;
  const d=e.data;
  if(d.name==='MoyBridgeCancel'){
   cancelled=true;s.textContent='Подключение отменено. Откройте решение снова.';return;
  }
  if(cancelled||d.name!=='MoyBridgeRequest'||busy||!Number.isInteger(d.request_id)||
     d.request_id<1||d.request_id>32||seen.has(d.request_id)||
     typeof d.project_id!=='string'||!uuid.test(d.project_id)||!offered.has(d.project_id))return;
  if(!['claim','options','save'].includes(d.action))return;
  if(d.action==='claim'&&(claimStarted||typeof d.code!=='string'||
     !/^[A-Za-z0-9_-]{20,64}$/.test(d.code)))return;
  if(d.action!=='claim'&&bound!==d.project_id)return;
  if(d.action==='save'&&(!options||
    !options.organizations.some(x=>x.id===d.organization_id)||
    !options.stores.some(x=>x.id===d.store_id)))return;
  seen.add(d.request_id);busy=true;
  const base='/api/projects/'+d.project_id+'/app-integrations/moysklad/';
  let path=base+'options',init={credentials:'same-origin',redirect:'error',
  signal:AbortSignal.timeout(12000)};
  if(d.action==='claim'){
   claimStarted=true;path=base+'claim';
   init={...init,method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({code:d.code})};
  }else if(d.action==='save'){
   path=base+'settings';init={...init,method:'PUT',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({organization_id:d.organization_id,store_id:d.store_id})};
  }
  try{
   const check=await fetch('/api/integrations/moysklad/connect/session',{
    credentials:'same-origin',redirect:'error',signal:AbortSignal.timeout(12000)});
   const session=check.ok?await check.json():null;
   if(session?.session_marker!==bridge.session_marker){
    cancelled=true;reply(d.request_id,'auth_changed');return;
   }
   if(cancelled){reply(d.request_id,'unknown');return;}
   const r=await fetch(path,init);const v=await r.json();
   if(!r.ok){reply(d.request_id,'error');return;}
   if(cancelled){reply(d.request_id,'unknown');return;}
   if(d.action==='claim'){
    if(!['connected','vendor_sync_pending'].includes(v?.status))throw Error();
    bound=d.project_id;reply(d.request_id,v.status);
   }else if(d.action==='options'){
    if(!validOptions(v))throw Error();options=v;
    reply(d.request_id,'options',{organizations:v.organizations,stores:v.stores});
   }else{
    if(v?.status!=='saved')throw Error();reply(d.request_id,'saved');
   }
  }catch{reply(d.request_id,'unknown');}
  finally{busy=false;}
 });
 if(!opener){s.textContent='Откройте вход из решения Yleum в МойСклад.';return;}
 opener.postMessage({name:'MoyBridgeReady',nonce:bridge.nonce,projects:bridge.projects,
  account_label:bridge.account_label},origin);
})();
</script></html>""".replace("BOOTSTRAP", bootstrap)
    return _iframe_page(page, frame_ancestors="'none'")


@router.get("/api/integrations/moysklad/setup", response_class=HTMLResponse)
async def setup() -> HTMLResponse:
    page = """<!doctype html><html lang=ru><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Подключить МойСклад к Yleum</title>
<style>
body{font:16px system-ui;margin:2rem;max-width:38rem;color:#172034}
p{line-height:1.5}label,select,button{display:block;margin:.75rem 0}
select,button{font:inherit;padding:.6rem;max-width:100%}
</style>
<h1>Подключить МойСклад к Yleum</h1>
<p id="status" role="status">Подтверждаем аккаунт…</p>
<button id="auth" disabled>Войти в Yleum</button>
<div id="result" hidden>
<p id="account"></p>
<label for="project">Ваш миниапп Yleum</label><select id="project"></select>
<button id="connect" disabled>Подключить выбранный миниапп</button>
</div>
<div id="settings" hidden>
<label for="organization">Организация</label><select id="organization"></select>
<label for="store">Склад</label><select id="store"></select>
<button id="save" disabled>Сохранить настройки</button>
</div>
<p><a href="/api/integrations/moysklad/return" target=_blank
rel="noopener noreferrer">Открыть кабинет Yleum</a></p>
<script nonce="SCRIPT_NONCE">
(()=>{
 const id=Math.floor(Math.random()*2147483647);
 const origin=window.location?.origin;
 const el=id=>document.getElementById(id);
 const s=el('status');
 let code=null,popup=null,nonce=null,projects=[],bound=null,pending=null,counter=0;
 let claimStarted=false,contextConfirmed=false;
 const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
 const list=(items,max)=>Array.isArray(items)&&items.length<=max&&items.every(x=>x&&
  typeof x.id==='string'&&uuid.test(x.id)&&typeof x.name==='string'&&x.name.length<=200);
 const render=(id,items)=>{
  const select=el(id);select.replaceChildren();
  for(const item of items){const option=document.createElement('option');
   option.value=item.id;option.textContent=item.name;select.appendChild(option);}
 };
 const request=(action,extra={})=>{
  if(!popup||!nonce||pending||counter>=32||(action==='claim'&&claimStarted))return;
  const project=action==='claim'?el('project').value:bound;
  if(!projects.some(x=>x.id===project))return;
  pending={id:++counter,action,project_id:project};
  if(action==='claim'){claimStarted=true;code=null;el('project').disabled=true;}
  el('connect').disabled=true;el('save').disabled=true;
  popup.postMessage({name:'MoyBridgeRequest',nonce,request_id:pending.id,action,
   project_id:project,...extra},origin);
 };
 el('auth').onclick=()=>{
  if(claimStarted||!code||!origin||(popup&&!popup.closed))return;
  popup=window.open('/api/integrations/moysklad/connect','_blank');
  if(!popup){s.textContent='Браузер заблокировал окно входа. Разрешите его и повторите вход.';
   return;}
  nonce=null;projects=[];bound=null;pending=null;counter=0;
  el('auth').disabled=true;s.textContent='Войдите в Yleum в открытом окне.';
  const observer=setInterval(()=>{
   if(!popup||popup.closed){clearInterval(observer);popup=null;nonce=null;pending=null;
    el('connect').disabled=true;el('save').disabled=true;el('auth').disabled=claimStarted;
    s.textContent='Окно входа закрыто. Проверьте подключение перед повтором.';}
  },1000);
 };
 el('connect').onclick=()=>{if(code)request('claim',{code});};
 el('save').onclick=()=>request('save',{organization_id:el('organization').value,
  store_id:el('store').value});
 window.addEventListener('message',e=>{
  if(!origin||!popup||popup.closed||e.origin!==origin||e.source!==popup||!e.data)return;
  const d=e.data;
  if(d.name==='MoyBridgeReady'){
   if(nonce||typeof d.nonce!=='string'||!/^[A-Za-z0-9_-]{20,64}$/.test(d.nonce)||
     !list(d.projects,100)||new Set(d.projects.map(x=>x.id)).size!==d.projects.length||
     typeof d.account_label!=='string'||d.account_label.length>254)return;
   nonce=d.nonce;projects=d.projects;render('project',projects);
   el('account').textContent='Аккаунт Yleum: '+d.account_label;
   el('result').hidden=false;el('connect').disabled=projects.length===0;
   s.textContent=projects.length?'Выберите миниапп и нажмите «Подключить».':
    'В этом аккаунте нет миниаппов. Создайте миниапп в Yleum и переоткройте решение.';return;
  }
  if(d.name==='MoyBridgeExpired'&&d.nonce===nonce){
   code=null;claimStarted=true;pending=null;el('auth').disabled=true;
   el('connect').disabled=true;el('save').disabled=true;
   s.textContent='Срок окна истёк. Проверьте исход действия и переоткройте решение.';return;
  }
  if(d.name!=='MoyBridgeResult'||d.nonce!==nonce||!pending||d.request_id!==pending.id)return;
  const action=pending.action,target=pending.project_id;pending=null;
  if(action==='claim'&&['connected','vendor_sync_pending'].includes(d.status)){
   bound=target;code=null;el('project').disabled=true;
   s.textContent=d.status==='connected'?'Миниапп подключён. Выберите организацию и склад.':
    'Миниапп связан; подтверждение МойСклад задержалось. Выберите организацию и склад.';
   request('options');return;
  }
  if(action==='options'&&d.status==='options'&&list(d.organizations,1000)&&list(d.stores,1000)){
   render('organization',d.organizations);render('store',d.stores);el('settings').hidden=false;
   el('save').disabled=!d.organizations.length||!d.stores.length;return;
  }
  if(action==='save'&&d.status==='saved'){
   s.textContent='Настройки организации и склада сохранены.';el('save').disabled=false;return;
  }
  if(d.status==='auth_changed'){code=null;claimStarted=true;
   el('auth').disabled=true;
   s.textContent='Аккаунт или сеанс Yleum изменился. Переоткройте решение.';return;}
  s.textContent=d.status==='error'?'Не удалось выполнить действие. Переоткройте решение.':
   'Исход действия неизвестен. Проверьте подключение перед повтором.';
 });
 window.addEventListener('pagehide',()=>{
  if(popup&&nonce)popup.postMessage({name:'MoyBridgeCancel',nonce},origin);
 });
 const h=async e=>{
  if(e.origin!=='https://online.moysklad.ru'||e.source!==window.parent||
     e.data?.name!=='UserContextResponse'||e.data.correlationId!==id)return;
  window.removeEventListener('message',h);
  try{
   if(typeof e.data.token!=='string'||!e.data.token)throw Error();
   const r=await fetch('/api/integrations/moysklad/context',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({token:e.data.token}),credentials:'omit',redirect:'error',
    signal:globalThis.AbortSignal?.timeout?.(12000)
   });const v=await r.json();
   if(!r.ok||typeof v.code!=='string'||!/^[A-Za-z0-9_-]{20,64}$/.test(v.code))throw Error();
   code=v.code;contextConfirmed=true;el('auth').disabled=false;
   s.textContent='МойСклад подтверждён. Войдите в Yleum, чтобы выбрать свой миниапп.';
   setTimeout(()=>{code=null;el('connect').disabled=true;
    if(!bound)s.textContent='Срок подтверждения истёк. Переоткройте решение.';},600000);
  }catch{s.textContent='Не удалось подтвердить аккаунт. Переоткройте решение.';}
 };
 window.addEventListener('message',h);
 window.parent.postMessage({name:'UserContextRequest',messageId:id},'https://online.moysklad.ru');
 setTimeout(()=>{if(!contextConfirmed){
  s.textContent='МойСклад не ответил. Переоткройте решение.';}},12000);
})();
</script></html>"""
    return _iframe_page(page, frame_ancestors="https://online.moysklad.ru")


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
