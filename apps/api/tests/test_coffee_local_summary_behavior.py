from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace as NS

import pytest

from yleum_api.services import max_behavior_browser as browser
from yleum_api.services import max_behavior_proof as b

from .behavior_browser_fixture import installed_chromium
from .test_max_behavior_browser import local_fixture as local_fixture
from .test_max_behavior_proof import coordinator_harness

REQUEST = "Добавь кнопку «Проверить заявку»: клиентское резюме без отправки."
HTML = """<!doctype html><html><head><meta charset="UTF-8">
<link rel="stylesheet" href="/_next/static/css/a.css"></head>
<body><form id="request"><input type="text" id="input" value="synthetic existing">
<button type="button" id="check">Проверить заявку</button></form>
<section id="summary"></section>
<script src="/_next/static/chunks/a.js"></script></body></html>""".encode()

CSS = b"button,input{min-width:44px;min-height:44px}body{margin:12px}#summary{min-height:44px}"
JS = (
    b"document.querySelector('#check').onclick=()=>{"
    b"document.querySelector('#summary').textContent=document.querySelector('#input').value};"
)


def register(server, mutation):
    html, css, js = HTML, CSS, JS
    if mutation in {"comments-only", "literal-only"}:
        html = html.replace(html[html.index(b"<button") : html.index(b"</button>") + 9], b"")
        js = (
            "// Проверить заявку\n"
            if mutation == "comments-only"
            else 'const unused = "Проверить заявку";'
        ).encode()
    elif mutation == "hidden":
        css += b"#check{opacity:0}"
    elif mutation == "handler":
        js = b"document.querySelector('#check').onclick=()=>{};"
    elif mutation == "static":
        js = js.replace(b"document.querySelector('#input').value", b"'synthetic constant'")
    elif mutation == "clear-form":
        js = js.replace(b".value};", b".value;document.querySelector('#input').value=''};")
    elif mutation == "POST":
        js += (
            b"document.querySelector('#check').addEventListener('click',"
            b"()=>fetch('/business',{method:'POST'}));"
        )
    elif mutation == "provider":
        js += b"document.querySelector('#check').addEventListener('click',()=>fetch('https://unapproved.invalid/model'));"
    server.files = {"/": html, "/_next/static/css/a.css": css, "/_next/static/chunks/a.js": js}

    async def resolver(request):
        return b.CompiledAssetWitness(
            request.binding,
            tuple(
                b.ObservedAsset(path, hashlib.sha256(body).hexdigest(), len(body))
                for path, body in server.files.items()
                if path != "/"
            ),
            "f" * 64,
            "served_candidate_compilation_v1",
        )

    return browser.make_private_browser_driver(
        executable_path=installed_chromium(),
        adapter=browser.PlatformBrowserAdapter(
            coffee_summary=browser.CoffeeSummaryAdapter("#check", "#request", "#input", "#summary")
        ),
        resolve_candidate_compilation=resolver,
        launch_args=("--no-sandbox",),
    )


def execute(monkeypatch, server, mutation=""):
    c = b.required_contract(REQUEST, template="max_miniapp")
    coordinator, identity, candidate, run, events, permit = coordinator_harness(
        monkeypatch, registered=register(server, mutation), frozen=c
    )
    action = coordinator._prepare_and_promote(
        identity,
        NS(artifact_ref=permit.build_ref),
        NS(artifact_ref=permit.verification_ref),
        permit,
    )
    return action, candidate, run, events, c


def test_known_coffee_request_is_a_named_contract_not_source_acceptance():
    c = b.required_contract(REQUEST, template="max_miniapp")
    assert c.capabilities == ("coffee_local_summary_v1",)
    assert (
        b.required_contract(
            "Не добавляй кнопку Проверить заявку: клиентское резюме без отправки",
            template="max_miniapp",
        )
        is None
    )


def test_actual_coffee_summary_fixture_wired_green_without_customer_values(
    local_fixture, monkeypatch
):
    action, candidate, run, events, _c = execute(monkeypatch, local_fixture)
    asyncio.run(action)
    assert candidate.status == "accepted" and "promote" in events
    receipt = run.agent_state[b.RECEIPT_KEY]
    assert receipt["status"] == "PASS_OBSERVED"
    assert receipt["capabilities"] == ["coffee_local_summary_v1"]
    assert "synthetic existing" not in str(receipt)
    assert local_fixture.mutations == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "comments-only",
        "literal-only",
        "hidden",
        "handler",
        "static",
        "clear-form",
        "POST",
        "provider",
    ],
)
def test_actual_coffee_missing_or_unsafe_behavior_cannot_promote(
    local_fixture, monkeypatch, mutation
):
    action, candidate, run, events, _contract = execute(monkeypatch, local_fixture, mutation)
    with pytest.raises(b.BehaviorProofError):
        asyncio.run(action)
    assert candidate.status == "prepared" and "promote" not in events
    receipt = run.agent_state[b.RECEIPT_KEY]
    assert receipt["status"] == (
        "NEEDS_REVIEW" if mutation in {"POST", "provider"} else "NEEDS_CHANGES"
    )
    assert receipt["code"] != "BEHAVIOR_DRIVER_FAILED"
    assert local_fixture.mutations == 0


def test_exact_known_button_summary_no_send_selects_without_client_adjective():
    request = "Добавь кнопку «Проверить заявку»: показывай резюме полей без отправки данных."
    assert "клиентск" not in request and "локаль" not in request
    selected = b.required_contract(request, template="max_miniapp")
    assert selected is not None
    assert selected.capabilities == ("coffee_local_summary_v1",)
    assert selected.request_sha256 == hashlib.sha256(request.encode()).hexdigest()


@pytest.mark.parametrize("prefix", ["Do not add", "Don't add", "Avoid adding", "Without adding"])
def test_negative_english_known_coffee_button_does_not_require_ui(prefix):
    request = prefix + " the button «Проверить заявку» for a резюме без отправки данных."
    assert b.required_contract(request, template="max_miniapp") is None


def test_coffee_no_send_qualifier_after_button_remains_positive():
    request = "Add button «Проверить заявку» for a резюме, without sending, без отправки данных."
    selected = b.required_contract(request, template="max_miniapp")
    assert selected is not None and selected.capabilities == ("coffee_local_summary_v1",)
