from __future__ import annotations

import asyncio
import hashlib
import http.server
import threading
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from yleum_api.services import max_behavior_browser as browser
from yleum_api.services import max_behavior_proof as b

from .test_max_behavior_proof import REQUEST, THEME, binding, coordinator_harness

CHROME = "/workspace/qa-tools/playwright/chromium-1234/chrome-linux64/chrome"


def adapter():
    return browser.PlatformBrowserAdapter(
        density=browser.DensityAdapter(
            "#normal",
            "#compact",
            "density",
            "#filter",
            "high",
            "button,select",
            browser.DensityView("#list", "#list-view", "#list-view .card"),
            browser.DensityView("#board", "#board-view", "#board-view .card"),
        ),
        theme=browser.ThemeAdapter(
            "#light", "#dark", "header", "#list-view .card", "#list-view .text", "theme", "#list"
        ),
    )


@pytest.fixture
def local_fixture(monkeypatch):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.server.mutations += 1
            self.send_response(405)
            self.end_headers()

        def do_GET(self):
            self.server.reads += 1
            if self.path == "/_fixture/bootstrap":
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", "fixture=private; Path=/; HttpOnly")
                self.end_headers()
                return
            body = self.server.files.get(self.path)
            self.send_response(200 if body is not None else 404)
            self.send_header(
                "Content-Type",
                "text/html"
                if self.path == "/"
                else "text/css"
                if self.path.endswith(".css")
                else "application/javascript",
            )
            self.end_headers()
            if body is not None:
                self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.files = {}
    server.mutations = server.reads = 0
    origin = f"http://127.0.0.1:{server.server_port}"
    # Only this isolated test replaces the real HTTPS/workspace session validator.
    # The product driver has no loopback switch, synthetic auth, or actor claim.
    monkeypatch.setattr(
        browser, "_origin", lambda request: (origin, origin + "/_fixture/bootstrap")
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def fixture_files(mutation):
    html = HTML.replace(b"/app.css", b"/_next/static/css/a.css").replace(
        b"/app.js", b"/_next/static/chunks/a.js"
    )
    css, js = CSS, JS
    if mutation == "density-handler":
        js = js.replace(
            b"$('#compact').onclick=()=>density('compact');", b"$('#compact').onclick=()=>{};"
        )
    if mutation == "density-style":
        css = css.replace(b".compact .view{gap:8px}.compact .card{padding:8px}", b"")
    if mutation == "density-storage":
        js = js.replace(b"localStorage.setItem('density',v);", b"")
    if mutation == "theme-handler":
        js = js.replace(b"$('#dark').onclick=()=>theme('dark');", b"$('#dark').onclick=()=>{};")
    if mutation == "theme-style":
        css = css.replace(b".dark{", b".unused{").replace(b".dark .card{", b".unused .card{")
    if mutation == "theme-storage":
        js = js.replace(b"localStorage.setItem('theme',v);", b"")
    if mutation == "opacity":
        css += b"header{opacity:0}#normal,#compact{opacity:0}"
    if mutation == "filter":
        css += b"#compact{filter:opacity(0)}"
    if mutation == "write":
        js = js.replace(
            b"$('#compact').onclick=()=>density('compact');",
            b"$('#compact').onclick=()=>{fetch('/mutation',{method:'POST'});density('compact')};",
        )
    if mutation == "missing-control":
        html = html.replace(b'<button id="compact">Compact</button>', b"")
        js = js.replace(b"$('#compact').onclick=()=>density('compact');", b"")
    return {"/": html, "/_next/static/css/a.css": css, "/_next/static/chunks/a.js": js}


def registered(server, mutation="", *, invalid_asset=False, unsupported=False):
    server.files = fixture_files(mutation)

    async def resolver(request):
        assets = tuple(
            b.ObservedAsset(path, hashlib.sha256(body).hexdigest(), len(body))
            for path, body in server.files.items()
            if path != "/"
        )
        if invalid_asset:
            assets = (replace(assets[0], sha256="0" * 64), *assets[1:])
        # A synthetic trusted fixture compilation, never a live build attestation.
        return b.CompiledAssetWitness(
            request.binding, assets, "f" * 64, "served_candidate_compilation_v1"
        )

    return browser.make_private_browser_driver(
        executable_path=CHROME,
        adapter=browser.PlatformBrowserAdapter() if unsupported else adapter(),
        resolve_candidate_compilation=resolver,
        launch_args=("--no-sandbox",),
    )


def test_executed_browser_sources_exactly_match_frozen_probe_version():
    assert browser.executed_probe_digest() == b.PROBE_SHA256


def test_actual_browser_wired_controller_green_with_synthetic_candidate(local_fixture, monkeypatch):
    c = b.required_contract(REQUEST + " " + THEME, template="max_miniapp")
    coordinator, identity, candidate, run, events, permit = coordinator_harness(
        monkeypatch, registered=registered(local_fixture), frozen=c
    )
    asyncio.run(
        coordinator._prepare_and_promote(
            identity,
            NS(artifact_ref=permit.build_ref),
            NS(artifact_ref=permit.verification_ref),
            permit,
        )
    )
    assert candidate.status == "accepted" and "promote" in events
    receipt = run.agent_state[b.RECEIPT_KEY]
    assert receipt["status"] == "PASS_OBSERVED"
    assert receipt["capabilities"] == ["task_density_v1", "header_theme_v1"]
    assert "Synthetic" not in str(receipt) and "PRIVATE_NEVER_EXPORT" not in str(receipt)
    assert local_fixture.reads > 0 and local_fixture.mutations == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "density-handler",
        "density-style",
        "density-storage",
        "theme-handler",
        "theme-style",
        "theme-storage",
        "missing-control",
        "opacity",
        "filter",
        "write",
    ],
)
def test_actual_browser_red_cannot_promote(local_fixture, monkeypatch, mutation):
    c = b.required_contract(REQUEST + " " + THEME, template="max_miniapp")
    coordinator, identity, candidate, run, events, permit = coordinator_harness(
        monkeypatch, registered=registered(local_fixture, mutation), frozen=c
    )
    with pytest.raises(b.BehaviorProofError):
        asyncio.run(
            coordinator._prepare_and_promote(
                identity,
                NS(artifact_ref=permit.build_ref),
                NS(artifact_ref=permit.verification_ref),
                permit,
            )
        )
    assert candidate.status == "prepared" and "promote" not in events
    receipt = run.agent_state[b.RECEIPT_KEY]
    expected = "NEEDS_REVIEW" if mutation in {"filter", "write"} else "NEEDS_CHANGES"
    assert receipt["status"] == expected
    assert receipt["code"] != "BEHAVIOR_DRIVER_FAILED"
    assert local_fixture.mutations == 0


@pytest.mark.parametrize("case", ["asset", "adapter"])
def test_invalid_compilation_or_adapter_never_passes(local_fixture, case):
    c = b.required_contract(REQUEST + " " + THEME, template="max_miniapp")
    with pytest.raises(b.BehaviorProofError):
        asyncio.run(
            b.observe_candidate(
                binding(c),
                c,
                registered(
                    local_fixture, invalid_asset=case == "asset", unsupported=case == "adapter"
                ),
                b.PrivatePreviewCapability(NS()),
            )
        )
    assert local_fixture.mutations == 0


HTML = (
    b'<!doctype html><html><head><link rel="stylesheet" href="/app.css"></head>\n<'
    b'body><header><button id="light">Light</button><button id="dark">Dark</butto'
    b'n></header>\n<main><button id="normal">Normal</button><button id="compact">C'
    b'ompact</button>\n<button id="list">List</button><button id="board">Board</bu'
    b'tton>\n<label>Priority<select id="filter"><option value="all">All</option><o'
    b'ption value="high">High</option></select></label>\n<section id="list-view" c'
    b'lass="view"></section><section id="board-view" class="view" hidden></sectio'
    b'n></main>\n<script src="/app.js"></script></body></html>'
)


CSS = (
    b"body{background:rgb(250,250,250);color:rgb(20,20,20);margin:12px}\nbutton,se"
    b"lect{min-width:44px;min-height:44px}header{display:flex;gap:8px}\n.view{disp"
    b"lay:flex;flex-direction:column;gap:24px;margin-top:12px}\n.card{padding:24px"
    b";background:rgb(255,255,255);color:rgb(30,30,30);border:1px solid gray}\n.co"
    b"mpact .view{gap:8px}.compact .card{padding:8px}\n.dark{background:rgb(20,20,"
    b"20);color:rgb(245,245,245)}\n.dark .card{background:rgb(40,40,40);color:rgb("
    b"235,235,235)}\n[hidden]{display:none!important}"
)


JS = (
    b"const $=s=>document.querySelector(s);let mode='list';\nfunction render(){for"
    b"(const v of ['list','board']){$('#'+v+'-view').hidden=v!==mode;\n$('#'+v+'-v"
    b"iew').innerHTML=['one','two','three'].filter((x,i)=>$('#filter').value==='a"
    b'll\'||i<2).map(x=>`<article class="card" data-item-id="${x}"><span class="te'
    b"xt\">Synthetic ${x}</span></article>`).join('');}}\nfunction density(v){docum"
    b"ent.body.classList.toggle('compact',v==='compact');localStorage.setItem('de"
    b"nsity',v);}\nfunction theme(v){document.body.classList.toggle('dark',v==='da"
    b"rk');localStorage.setItem('theme',v);}\n$('#normal').onclick=()=>density('no"
    b"rmal');$('#compact').onclick=()=>density('compact');\n$('#light').onclick=()"
    b"=>theme('light');$('#dark').onclick=()=>theme('dark');\n$('#list').onclick=("
    b")=>{mode='list';render()};$('#board').onclick=()=>{mode='board';render()};$"
    b"('#filter').onchange=render;\ndensity(localStorage.getItem('density')||'norm"
    b"al');theme(localStorage.getItem('theme')||'light');render();"
)
