"""Native public installation routes never pair accounts or contact providers."""

import asyncio
import json
import re
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from yleum_api.core.config import Settings
from yleum_api.core.db import get_session
from yleum_api.core.deps import get_current_user, get_optional_user
from yleum_api.core.errors import ApiError, api_error_handler
from yleum_api.core.security import decode_access_claims
from yleum_api.models.project import Project
from yleum_api.routers import moysklad_vendor

COOKIE = "__Host-moysklad-return"
RETURN = "/api/integrations/moysklad/return"
# Synthetic configured path, never fetched or presented as a real public listing.
INSTALL = "https://online.moysklad.ru/QA_SYNTHETIC_PUBLIC_LISTING"


@pytest.fixture
def native(monkeypatch):
    settings = SimpleNamespace(
        integration_moysklad_public_install_url=INSTALL,
        jwt_secret=SecretStr("QA_NATIVE_RETURN_LOCAL_ONLY_SECRET_32"),
        web_base_url="https://yleum.ru",
    )
    monkeypatch.setattr(moysklad_vendor, "get_settings", lambda: settings)
    user = SimpleNamespace(
        id=uuid4(),
        session_version=4,
        status="active",
        is_anon=False,
        email="qa@example.invalid",
        email_verified_at=datetime.now(UTC),
    )
    project = SimpleNamespace(id=uuid4(), owner_id=user.id, template="max_miniapp")
    state = SimpleNamespace(user=user, project=project, entitled=True, entitlement_checks=0)

    class ReadOnlySession:
        async def get(self, model, key):
            assert model is Project
            return project if state.project and key == project.id else None

        def add(self, *_args):
            pytest.fail("native navigation must not mutate integration state")

        async def commit(self):
            pytest.fail("native navigation must not commit")

    async def session_override():
        yield ReadOnlySession()

    async def current_override():
        if state.user is None:
            raise ApiError("unauthorized", "QA missing auth", 401)
        return state.user

    async def optional_override():
        return state.user

    async def entitlement(_session, _owner):
        state.entitlement_checks += 1
        if not state.entitled:
            raise ApiError("entitlement_exceeded", "QA denied", 403)

    monkeypatch.setattr(moysklad_vendor, "assert_integrations_allowed", entitlement)
    app = FastAPI()
    app.include_router(moysklad_vendor.router)
    app.add_exception_handler(ApiError, api_error_handler)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_current_user] = current_override
    app.dependency_overrides[get_optional_user] = optional_override
    with TestClient(app, base_url="https://yleum.ru", follow_redirects=False) as client:
        yield client, state, settings


def start(client, state):
    return client.post(f"/api/projects/{state.project.id}/app-integrations/moysklad/install/start")


def test_absent_listing_does_not_issue_cookie_or_install_link(native):
    client, state, settings = native
    settings.integration_moysklad_public_install_url = None
    info = client.get(f"/api/projects/{state.project.id}/app-integrations/moysklad/install")
    assert info.status_code == 200
    assert info.json() == {"available": False, "install_url": None}
    response = start(client, state)
    assert response.status_code == 503
    assert "set-cookie" not in response.headers


def test_start_and_return_use_owned_project_and_navigation_only_cookie(native):
    client, state, _settings = native
    response = start(client, state)
    assert response.status_code == 200
    assert response.json() == {"install_url": INSTALL}
    cookie = response.headers["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "Path=/" in cookie and "Domain=" not in cookie and "Max-Age=900" in cookie
    raw = client.cookies.get(COOKIE)
    assert decode_access_claims(raw) is None  # no cross-purpose login credential
    response = client.get(RETURN)
    assert response.status_code == 303
    assert (
        response.headers["location"]
        == f"/max/{state.project.id}?panel=services&integration=moysklad"
    )
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "fault", ["foreign_project", "unregistered", "unverified", "entitlement", "anonymous"]
)
def test_start_preserves_owner_access_and_entitlement_guards(native, fault):
    client, state, _settings = native
    if fault == "foreign_project":
        state.project.owner_id = uuid4()
    elif fault == "unregistered":
        state.user.is_anon = True
    elif fault == "unverified":
        state.user.email_verified_at = None
    elif fault == "entitlement":
        state.entitled = False
    else:
        state.user = None
    response = start(client, state)
    assert response.status_code in (401, 403, 404)
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    "fault",
    [
        "tampered",
        "expired",
        "foreign_owner",
        "session_rotated",
        "project_transferred",
        "project_deleted",
        "unverified",
        "entitlement",
    ],
)
def test_return_cannot_route_into_invalid_or_foreign_project(native, monkeypatch, fault):
    client, state, _settings = native
    project = state.project
    assert start(client, state).status_code == 200
    raw = client.cookies.get(COOKIE)
    if fault == "tampered":
        client.cookies.set(COOKIE, raw[:-5] + "AAAAA", domain="yleum.ru", path="/")
    elif fault == "expired":
        monkeypatch.setattr(moysklad_vendor.time, "time", lambda: 4102444800)
    elif fault == "foreign_owner":
        state.user = SimpleNamespace(**{**vars(state.user), "id": uuid4()})
    elif fault == "session_rotated":
        state.user.session_version += 1
    elif fault == "project_transferred":
        project.owner_id = uuid4()
    elif fault == "project_deleted":
        state.project = None
    elif fault == "unverified":
        state.user.email_verified_at = None
    elif fault == "entitlement":
        state.entitled = False
    response = client.get(RETURN)
    assert response.status_code == 303
    assert response.headers["location"] == "/max"
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_return_login_preserves_valid_intent_then_rechecks_owner(native):
    client, state, _settings = native
    assert start(client, state).status_code == 200
    user = state.user
    state.user = None
    response = client.get(RETURN)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Fapi%2Fintegrations%2Fmoysklad%2Freturn"
    assert "set-cookie" not in response.headers
    state.user = user
    assert str(state.project.id) in client.get(RETURN).headers["location"]


def test_missing_return_and_untrusted_redirect_input_use_fixed_owner_picker(native):
    client, _state, _settings = native
    response = client.get(RETURN + "?next=https://attacker.invalid&project_id=QA_OTHER")
    assert response.status_code == 303
    assert response.headers["location"] == "/max"


@pytest.mark.parametrize(
    "url",
    [
        "http://online.moysklad.ru/QA",
        "https://online.moysklad.ru.attacker.invalid/QA",
        "https://attacker.invalid/QA",
        "https://user:password@online.moysklad.ru/QA",
        "https://online.moysklad.ru:8443/QA",
        "https://online.moysklad.ru/QA#token",
        "https://online.moysklad.ru/QA\\@attacker.invalid",
        "https://online.moysklad.ru/QA\n",
    ],
)
def test_configuration_rejects_untrusted_or_ambiguous_install_urls(url):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://QA/QA",
            jwt_secret="QA_SECRET",
            integration_moysklad_public_install_url=url,
        )


def test_empty_install_configuration_remains_unavailable():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://QA/QA",
        jwt_secret="QA_SECRET",
        integration_moysklad_public_install_url="",
    )
    assert settings.integration_moysklad_public_install_url is None


@pytest.mark.asyncio
async def test_actual_setup_script_rejects_hostile_origin_before_context_request():
    response = await moysklad_vendor.setup()
    html = bytes(response.body).decode()
    script = re.search(r'<script nonce="[^"]+">(.*?)</script>', html, re.S).group(1)
    harness = """
const vm=require('node:vm'); let handler; let calls=0;
const elements=new Map();
const document={getElementById:id=>{
 if(!elements.has(id))elements.set(id,{hidden:true,textContent:''});
 return elements.get(id);
}};
const window={location:{origin:'https://yleum.ru'},addEventListener:(_n,h)=>{handler=h},removeEventListener:()=>{},
 parent:{postMessage:p=>{globalThis.correlation=p.messageId}}};
const fetch=async()=>{calls++;return{ok:true,
 json:async()=>({code:'QA_ONE_TIME_CODE_ONLY',account_name:'QA'})}};
vm.runInNewContext(SCRIPT,{document,window,fetch,Math,setTimeout:()=>{}});
(async()=>{
 await handler({origin:'https://attacker.invalid',data:{name:'UserContextResponse',correlationId:globalThis.correlation,token:'QA_CONTEXT'}});
 if(calls!==0)throw Error('hostile origin reached context');
 await handler({origin:'https://online.moysklad.ru',source:window.parent,data:{name:'UserContextResponse',correlationId:globalThis.correlation,token:'QA_CONTEXT'}});
 if(calls!==1)throw Error('trusted context not checked');
})();
""".replace("SCRIPT", json.dumps(script))
    run = await asyncio.to_thread(
        subprocess.run,
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert run.returncode == 0, run.stderr
    assert 'href="/api/integrations/moysklad/return"' in html


@pytest.mark.parametrize("host", ["online.moysklad.ru", "www.moysklad.ru", "moysklad.ru"])
def test_settings_accept_complete_reviewed_https_url_on_exact_official_hosts(host):
    value = f"https://{host}/QA_SYNTHETIC_PUBLIC_LISTING"
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://QA/QA",
        jwt_secret="QA_SECRET",
        integration_moysklad_public_install_url=value,
    )
    assert settings.integration_moysklad_public_install_url == value


def test_auth_token_cannot_be_used_as_return_intent(native):
    client, state, settings = native
    raw = jwt.encode(
        {
            "sub": str(state.user.id),
            "owner": str(state.user.id),
            "project": str(state.project.id),
            "ver": state.user.session_version,
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": 4102444800,
            "aud": "moysklad-native-return",
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    client.cookies.set(COOKIE, raw, domain="yleum.ru", path="/")
    assert client.get(RETURN).headers["location"] == "/max"


def test_foreign_project_cannot_discover_public_install_configuration(native):
    client, state, _settings = native
    state.project.owner_id = uuid4()
    response = client.get(f"/api/projects/{state.project.id}/app-integrations/moysklad/install")
    assert response.status_code == 404
    assert INSTALL not in response.text


def test_return_failure_does_not_hide_unexpected_backend_error(native, monkeypatch):
    client, state, _settings = native
    assert start(client, state).status_code == 200

    async def broken(_session, _owner):
        raise ApiError("integration_provider_unavailable", "QA_UNEXPECTED", 503)

    monkeypatch.setattr(moysklad_vendor, "assert_integrations_allowed", broken)
    response = client.get(RETURN)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "integration_provider_unavailable"
