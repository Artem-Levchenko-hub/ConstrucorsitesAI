"""Real Chromium exercises the bounded prepublication meal probe."""
from __future__ import annotations

import hashlib
import json
import ssl
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from tests.behavior_browser_fixture import installed_chromium

_DISHES = ("Овсяная каша", "Овощной суп", "Запеченная рыба")
_INGREDIENTS = ("Овсяные хлопья", "Морковь", "Треска", "Капуста")
_MEALS = ("breakfast", "lunch", "dinner")
_EXISTING = {"id": "existing", "ingredient_id": "existing", "name": "Картофель", "bought": True}
_ENDPOINT = "/api/meal/shopping"
_ACTOR = "9998110120030040"
_COOKIE = "synthetic.candidate.session"
_ORDER = "d26d58ba-2b74-4b6b-9dc4-2493c1d3e7ce"
_ASSET = "/_next/static/chunks/meal.js"
_PLATFORM_PATH = "/omnia-inspector.js"
_PLATFORM_SCRIPT = b"window.installedPlatformFixture = true;"
_HTML = '''<!DOCTYPE html><meta charset="utf-8"><main id="screen"></main>
<nav><button data-testid="meal-nav-plan">Рацион</button>
<button data-testid="meal-nav-shopping">Покупки</button>
<button data-testid="meal-nav-store">Магазин</button></nav>
<script src="/_next/static/chunks/meal.js"></script>'''.encode()


def _pdf(lines: tuple[str, ...]) -> bytes:
    """A small PDF with real text operations and a Cyrillic ToUnicode map."""
    chars = sorted(set("".join(lines)))
    encoding = {char: index + 33 for index, char in enumerate(chars)}
    mapping = b"\n".join(
        f"<{index:02X}> <{ord(char):04X}>".encode() for char, index in encoding.items()
    )
    cmap = (b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
            b"/CIDSystemInfo << /Registry (Test) /Ordering (Unicode) /Supplement 0 >> def\n"
            b"/CMapName /Fixture def /CMapType 2 def\n1 begincodespacerange\n<00><FF>\n"
            b"endcodespacerange\n" + f"{len(chars)} beginbfchar\n".encode() + mapping
            + b"\nendbfchar\nendcmap CMapName currentdict /CMap defineresource pop end end")
    content = b"BT /F0 14 Tf 24 220 Td " + b" 0 -23 Td ".join(
        b"<" + bytes(encoding[char] for char in line).hex().encode() + b"> Tj" for line in lines
    ) + b" ET"

    def stream(data: bytes) -> bytes:
        return f"<< /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 500 250] /Contents 4 0 R "
        b"/Resources << /Font << /F0 5 0 R >> >> >>",
        stream(content), b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 6 0 R >>",
        stream(cmap),
    ]
    output = b"%PDF-1.4\n"
    offsets = []
    for number, data in enumerate(objects, 1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + data + b"\nendobj\n"
    start = len(output)
    output += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    output += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n"
    return output + trailer.encode()


def _script(fault):
    fixtures = dict(zip(_MEALS, zip(_DISHES, _INGREDIENTS[:3], strict=True), strict=True))
    return ("const data=" + json.dumps(fixtures, ensure_ascii=False) + ";const fault="
            + json.dumps(fault) + r""";
const screen=document.querySelector('#screen');const path='/api/meal/shopping';
let filter=false;let tab='plan';
const test=(name)=>document.querySelector(`[data-testid="${name}"]`);
const req=(url,method,body)=>fetch(url,{method,headers:{'Content-Type':'application/json'},
  body:body===undefined?undefined:JSON.stringify(body)});
function plan(){tab='plan';screen.innerHTML=Object.keys(data)
  .filter(k=>k!=='dinner'||fault!=='meal')
  .map(k=>`<button data-testid="meal-open-${k}">${k}</button>`).join('')+
  '<a data-testid="meal-download-pdf" href="/api/meal/pdf" download>Export</a><section></section>';
  for(const [k,[dish,ingredient]] of Object.entries(data)){
    const button=test('meal-open-'+k);if(!button)continue;
    button.onclick=()=>{screen.querySelector('section').innerHTML=
     `<h2 data-testid="meal-dish-name">${dish}</h2><div data-testid="meal-ingredient-name">
      ${ingredient}</div><button data-testid="meal-add-ingredient" data-ingredient-id="${k}">
      Add</button>`+
      (k==='dinner'? '<div data-testid="meal-ingredient-name">Капуста</div>'+
        '<button data-testid="meal-add-ingredient" data-ingredient-id="extra">Add</button>':'');
      for(const control of screen.querySelectorAll('[data-ingredient-id]')){
        control.onclick=async()=>{await req(path,'POST',
          {ingredient_id:fault==='unsafe_write'?'unknown':control.dataset.ingredientId});};
      }
    };
  }
}
async function shopping(){tab='shopping';const rows=await (await fetch(path)).json();
 const filters='<button data-testid="meal-filter-all">Все</button>'+
 '<button data-testid="meal-filter-unbought">Не куплено</button>'+
 `<output data-testid="meal-remaining-count">${fault==='counter'?99:
    rows.filter(r=>!r.bought).length}</output>`;
 screen.innerHTML=(fault==='no_filters'?'':filters)+rows.filter(r=>!filter||!r.bought||fault==='filter').map(r=>
  `<article data-testid="meal-shopping-row" data-item-id="${r.id}">${r.name}
   <button data-testid="meal-shopping-toggle" aria-checked="${r.bought}">Toggle</button></article>`
 ).join('');
 if(fault!=='no_filters'){
   test('meal-filter-all').onclick=()=>{filter=false;shopping();};
   test('meal-filter-unbought').onclick=()=>{filter=true;shopping();};
 }
 for(const row of screen.querySelectorAll('[data-item-id]')){
   const item=rows.find(r=>r.id===row.dataset.itemId);
   row.querySelector('button').onclick=async()=>{
     await req(path+'/'+item.id,'PATCH',{bought:!item.bought});await shopping();};}
}
test('meal-nav-plan').onclick=plan;test('meal-nav-shopping').onclick=shopping;
let actor=null;let order=null;
let catalog=null;
const integration=(endpoint,id)=>req('/api/omnia/integrations/'+endpoint,'POST',{
  expectedActor:actor,payload:{order_id:id},
});
test('meal-nav-store').onclick=()=>{
  tab='store';screen.innerHTML='<section data-testid="meal-pack-info">Описание набора</section>';
  if(!fault.startsWith('wiring')) return;
  catalog=fetch('/api/meal/catalog').then(response=>response.json()).catch(()=>null);
  screen.innerHTML+='<button data-testid="meal-buy-pack">Купить</button>'+
    '<button data-testid="meal-check-checkout" hidden>Проверить оплату</button>';
  test('meal-buy-pack').onclick=async()=>{
    if(fault==='wiring_order'){
      await integration('order-checkout-status','412d4805-4259-4438-aed3-cd616ae90fe2');return;
    }
    if(fault==='wiring_get'){await fetch('/api/omnia/business/orders');return;}
    const item=await catalog;if(!item)return;
    const body={operationKey:crypto.randomUUID(),customerName:'Покупатель',
      configVersion:fault==='wiring_version'?item.configVersion+1:item.configVersion,
      items:[{catalogId:fault==='wiring_sku'?'wrong-pack':'meal-month-pack',
        quantity:1,unitPriceKopecks:1250}]};
    if(fault==='wiring_contacts')body.contactPhone='12345678';
    const created=await fetch('/api/omnia/business/orders',{method:'POST',
      headers:{'Content-Type':'application/json','X-Max-Expected-Actor':actor},
      body:JSON.stringify(body)});
    if(!created.ok)return;
    order=(await created.json()).id;
    if(fault==='wiring_wrong_order')order='412d4805-4259-4438-aed3-cd616ae90fe2';
    const pending=await integration('order-checkout',order);
    if(pending.ok)test('meal-check-checkout').hidden=false;
  };
  test('meal-check-checkout').onclick=async()=>{
    const status=await integration('order-checkout-status',order);
    if(!status.ok||(await status.json()).status!=='succeeded')return;
    const proof=await integration('order-checkout-receipt',order);
    if(!proof.ok||fault==='wiring_no_grant')return;
    const {receipt}=await proof.json();
    await req('/api/meal/entitlement','POST',{receipt});
  };
};
if(fault==='egress') fetch('https://elsewhere.invalid/never').catch(()=>{});
if(location.protocol==='https:') {
  fetch('/api/omnia/config').then(response=>response.json());
  fetch('/api/max/session').then(response=>response.json()).then(profile=>{
    if(profile.user){actor=profile.user.id;plan();}
  });
} else plan();
""").encode()


@pytest.fixture
def meal_server(request, tmp_path):
    secure = bool(getattr(request, "param", False))
    state = {"fault": "", "rows": {"existing": dict(_EXISTING)}, "writes": [], "sessions": 0}
    state["session_reads"] = 0
    state["checkout_upstream"] = []
    state["catalog_reads"] = 0
    lock = threading.RLock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def authenticated(self):
            cookies = self.headers.get("Cookie", "").split("; ")
            expected = "__Host-max_session=" + _COOKIE if secure else "meal=owner"
            return expected in cookies

        def send(self, value, media="application/json", headers=None):
            raw = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(raw)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            with lock:
                if self.path == "/bootstrap":
                    state["sessions"] += 1
                    return self.send(b"OK", "text/plain", {"Set-Cookie": "meal=owner; Path=/"})
                if not self.authenticated():
                    return self.send_error(403)
                if self.path == "/api/omnia/config":
                    return self.send({"legal": {"age_rating": "0+"}})
                if self.path == "/api/meal/catalog":
                    state["catalog_reads"] += 1
                    amount = 1 if state["fault"] == "wiring_catalog" else 1250
                    return self.send({"configVersion": 9, "sku": "meal-month-pack",
                                      "unitPriceKopecks": amount})
                if self.path == "/api/max/session":
                    state["session_reads"] += 1
                    actor = "preview" if state["fault"] == "session_identity" else _ACTOR
                    return self.send({"mode": "acceptance", "user": {"id": actor}})
                if self.path == "/":
                    body = _HTML
                    if state["fault"] in {"stale_platform", "pinned_platform"}:
                        body += b'<script src="/omnia-inspector.js"></script>'
                    return self.send(body, "text/html; charset=utf-8")
                if self.path == _PLATFORM_PATH:
                    script = _PLATFORM_SCRIPT
                    if state["fault"] == "stale_platform":
                        script += b"/* substituted */"
                    return self.send(script, "text/javascript")
                if self.path == _ASSET:
                    return self.send(_script(state["fault"]), "text/javascript")
                if self.path == _ENDPOINT:
                    rows = list(state["rows"].values())
                    if state["fault"] == "fresh_context" and state["sessions"] > 1:
                        rows = [row for row in rows if not row["id"].startswith("selected-")]
                    return self.send(rows)
                if self.path.startswith(("/api/omnia/business", "/api/omnia/integrations")) or (
                    self.path == "/api/meal/entitlement"
                ):
                    state["checkout_upstream"].append(("GET", self.path))
                    return self.send_error(500)
                if self.path == "/api/meal/pdf":
                    lines = ("Рацион на месяц", *_DISHES, *_INGREDIENTS)
                    if state["fault"] == "wrong_pdf":
                        lines = lines[:-1]
                    return self.send(_pdf(lines), "application/pdf", {
                        "Content-Disposition": 'attachment; filename="factual-meal.pdf"',
                    })
                self.send_error(404)

        def mutate(self, method):
            with lock:
                if not self.authenticated():
                    return self.send_error(403)
                if self.path.startswith(("/api/omnia/business", "/api/omnia/integrations")) or (
                    self.path == "/api/meal/entitlement"
                ):
                    state["checkout_upstream"].append((method, self.path))
                    return self.send_error(500)
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                state["writes"].append((method, self.path))
                row_id = self.path.rsplit("/", 1)[1]
                if method == "POST":
                    identifier = json.loads(body)["ingredient_id"]
                    row_id = "selected-" + identifier
                    name = _INGREDIENTS[(*_MEALS, "extra").index(identifier)]
                    item = {"id": row_id, "ingredient_id": identifier,
                            "name": name, "bought": False}
                    state["rows"][row_id] = item
                    return self.send(item)
                if method == "PATCH":
                    item = state["rows"][row_id]
                    if state["fault"] != "toggle":
                        item["bought"] = json.loads(body)["bought"]
                    return self.send(item)
                del state["rows"][row_id]
                return self.send({"deleted": True})

        def do_POST(self):
            self.mutate("POST")

        def do_PATCH(self):
            self.mutate("PATCH")

        def do_DELETE(self):
            self.mutate("DELETE")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    if secure:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(UTC)
        actor = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        cert = (x509.CertificateBuilder().subject_name(actor).issuer_name(actor)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1))
                .not_valid_after(now + timedelta(minutes=10)).sign(key, hashes.SHA256()))
        cert_path, key_path = tmp_path / "browser.pem", tmp_path / "browser.key"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert_path, key_path)
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    protocol = "https" if secure else "http"
    origin = f"{protocol}://127.0.0.1:{server.server_port}"

    def payload(mode, fault=""):
        state["fault"] = fault
        script = _script("" if fault == "stale_asset" else fault)
        value = {"origin": origin, "bootstrap": origin + "/bootstrap", "mode": mode,
                 "assets": [{"path": _ASSET, "sha256": hashlib.sha256(script).hexdigest(),
                             "bytes": len(script)}], "platform_assets": [],
                 "executable_path": installed_chromium(), "launch_args": []}
        if mode == "edit" and not secure:
            prior = {"id": "prior", "ingredient_id": "prior", "name": "Свёкла", "bought": False}
            with lock:
                state["rows"]["prior"] = prior
            value["prior_shopping"] = [dict(_EXISTING), dict(prior)]
        if fault in {"stale_platform", "pinned_platform"}:
            value["platform_assets"] = [{
                "path": _PLATFORM_PATH, "bytes": len(_PLATFORM_SCRIPT),
                "sha256": hashlib.sha256(_PLATFORM_SCRIPT).hexdigest(),
            }]
        if secure:
            value.update(
                buyer_cookie=_COOKIE, buyer_id=_ACTOR,
                launch_args=["--ignore-certificate-errors"],  # Loopback test certificate only.
            )
        return value
    try:
        yield payload, state
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()


@pytest.mark.parametrize(("mode", "fault"), [("build", "pinned_platform"), ("edit", "")])
def test_actual_browser_accepts_meals_shopping_and_download(meal_server, mode, fault):
    from yleum_api.services.meal_acceptance_browser import BUILD_CHECKS, EDIT_CHECKS, observe_meal

    payload, state = meal_server
    assert observe_meal(payload(mode, fault)) == dict.fromkeys(
        EDIT_CHECKS if mode == "edit" else BUILD_CHECKS, True,
    )
    assert set(state["rows"]) == ({"existing", "prior"} if mode == "edit" else {"existing"})
    assert state["rows"]["existing"] == _EXISTING
    assert state["sessions"] == 2
    assert [item[0] for item in state["writes"]] == (
        ["POST", "POST", "PATCH"] + (["PATCH"] if mode == "edit" else []) + ["DELETE", "DELETE"]
    )


@pytest.mark.parametrize(("fault", "check"), [
    ("meal", "three_meals"), ("wrong_pdf", "pdf_download"), ("toggle", "shopping_toggle"),
    ("fresh_context", "shopping_fresh_context"), ("filter", "shopping_filters"),
    ("counter", "shopping_remaining_count"),
])
def test_actual_browser_rejects_known_product_failures(meal_server, fault, check):
    from yleum_api.services.meal_acceptance_browser import MealObservationFailed, observe_meal

    payload, state = meal_server
    with pytest.raises(MealObservationFailed) as exc:
        observe_meal(payload("edit", fault))
    assert exc.value.check == check
    assert state["rows"]["existing"] == _EXISTING


@pytest.mark.parametrize("fault", ["stale_asset", "stale_platform", "egress", "unsafe_write"])
def test_browser_never_reports_candidate_red_for_unknown_execution(meal_server, fault):
    from yleum_api.services.generation.recovery_contract import RecoveryContractError
    from yleum_api.services.meal_acceptance_browser import MealObservationFailed, observe_meal

    payload, state = meal_server
    with pytest.raises(RecoveryContractError) as exc:
        observe_meal(payload("build", fault))
    assert not isinstance(exc.value, MealObservationFailed)
    assert state["writes"] == []


def test_owner_preview_cannot_substitute_for_isolated_buyer(meal_server):
    from yleum_api.services.generation.recovery_contract import RecoveryContractError
    from yleum_api.services.meal_acceptance_browser import observe_meal

    payload, state = meal_server
    candidate = payload("build")
    candidate.update(origin="https://private.invalid", bootstrap="https://private.invalid/start")
    with pytest.raises(RecoveryContractError):
        observe_meal(candidate)
    assert state["sessions"] == 0 and state["writes"] == []


@pytest.mark.parametrize("meal_server", [True], indirect=True)
@pytest.mark.parametrize("fault", ["", "session_identity"])
def test_https_probe_requires_the_signed_buyer_session(meal_server, fault, monkeypatch):
    from playwright.sync_api import Browser

    from yleum_api.services.generation.recovery_contract import RecoveryContractError
    from yleum_api.services.meal_acceptance_browser import BUILD_CHECKS, observe_meal

    payload, state = meal_server
    original_context = Browser.new_context

    # The fixture is loopback with a disposable self-signed certificate.
    def fixture_context(self, **kwargs):
        return original_context(self, **kwargs, ignore_https_errors=True)

    monkeypatch.setattr(Browser, "new_context", fixture_context)
    if fault:
        with pytest.raises(RecoveryContractError):
            observe_meal(payload("build", fault))
        assert state["writes"] == []
    else:
        assert observe_meal(payload("build")) == dict.fromkeys(BUILD_CHECKS, True)
        assert state["session_reads"] >= 3
    assert state["sessions"] == 0


@pytest.mark.parametrize("meal_server", [True], indirect=True)
@pytest.mark.parametrize(
    ("mode", "lost"), [("build", False), ("edit", False), ("edit", True)],
)
def test_reobserve_checks_accepted_actor_rows_before_writes(meal_server, mode, lost, monkeypatch):
    from playwright.sync_api import Browser

    from yleum_api.services.meal_acceptance_browser import (
        BUILD_CHECKS,
        EDIT_CHECKS,
        MealObservationFailed,
        observe_meal,
    )

    original_context = Browser.new_context
    monkeypatch.setattr(Browser, "new_context", lambda self, **kwargs: original_context(
        self, **kwargs, ignore_https_errors=True,
    ))
    payload, state = meal_server
    capture = {}
    assert observe_meal({**payload("build"), "retain_build_fixture": True}, capture=capture) == (
        dict.fromkeys(BUILD_CHECKS, True)
    )
    retained = capture["shopping"]
    assert len(retained) == 2 and sorted(row["bought"] for row in retained) == [False, True]
    assert {row["id"] for row in retained} == state["rows"].keys() - {"existing"}
    state["writes"].clear()
    if lost:
        del state["rows"][retained[0]["id"]]
    starting = {key: dict(value) for key, value in state["rows"].items()}
    fault = "no_filters" if mode == "build" else ""
    probe = {**payload(mode, fault), "prior_shopping": retained}
    if lost:
        with pytest.raises(MealObservationFailed) as failure:
            observe_meal(probe)
        assert failure.value.check == "preserve_existing"
        assert not state["writes"]
    else:
        second_capture = {}
        checks = EDIT_CHECKS if mode == "edit" else BUILD_CHECKS
        assert observe_meal(probe, capture=second_capture) == dict.fromkeys(checks, True)
        assert second_capture["shopping"] == retained
        if mode == "build":
            assert [method for method, _ in state["writes"]] == [
                "POST", "POST", "PATCH", "DELETE", "DELETE",
            ]
        assert all(path.rsplit("/", 1)[-1] not in starting for _, path in state["writes"])
    assert state["rows"] == starting


def test_known_entitlement_red_cleans_browser_rows_before_retry(meal_server):
    from yleum_api.services.meal_acceptance_browser import observe_meal
    from yleum_api.services.meal_acceptance_entitlement import MealEntitlementFailure

    payload, state = meal_server
    checked = {}
    failure = MealEntitlementFailure("entitlement_ten_recipes")

    def ready():
        assert state["sessions"] == 2
        assert [item[0] for item in state["writes"]] == ["POST", "POST", "PATCH"]
        raise failure

    with pytest.raises(MealEntitlementFailure) as exc:
        observe_meal({**payload("build"), "retain_build_fixture": True},
                     capture=checked, on_ready=ready)
    assert exc.value is failure
    assert checked == {}
    assert state["rows"] == {"existing": _EXISTING}
    assert [item[0] for item in state["writes"]] == ["POST", "POST", "PATCH", "DELETE", "DELETE"]


def test_edit_without_accepted_rows_fails_before_any_access(meal_server):
    from yleum_api.services.generation.recovery_contract import RecoveryContractError
    from yleum_api.services.meal_acceptance_browser import observe_meal

    payload, state = meal_server
    edited = payload("edit")
    del edited["prior_shopping"]
    with pytest.raises(RecoveryContractError):
        observe_meal(edited)
    assert not state["writes"] and not state["sessions"]


@pytest.mark.parametrize("meal_server", [True], indirect=True)
@pytest.mark.parametrize(("mode", "fault"), [
    ("build", "wiring"), ("edit", "wiring"), ("build", "wiring_sku"),
    ("build", "wiring_contacts"), ("build", "wiring_order"), ("build", "wiring_no_grant"),
    ("build", "wiring_wrong_order"), ("build", "wiring_get"), ("build", "wiring_catalog"),
    ("build", "wiring_version"),
])
def test_checkout_wiring_isolated_from_candidate_writes(meal_server, mode, fault, monkeypatch):
    from playwright.sync_api import Browser

    from yleum_api.services.meal_acceptance_browser import MealObservationFailed, observe_meal

    old_context = Browser.new_context
    monkeypatch.setattr(Browser, "new_context", lambda self, **kwargs: old_context(
        self, **kwargs, ignore_https_errors=True,
    ))
    payload, state = meal_server
    args = {**payload(mode, fault), "check_checkout_wiring": True, "order_id": _ORDER}
    if mode == "edit":
        prior = {"id": "prior", "ingredient_id": "prior", "name": "Свёкла", "bought": False}
        state["rows"]["prior"] = prior
        args["prior_shopping"] = [dict(_EXISTING), dict(prior)]
    before = state["rows"].copy()
    if fault == "wiring":
        assert observe_meal(args)["checkout_wiring"] is True
    else:
        with pytest.raises(MealObservationFailed) as failure:
            observe_meal(args)
        assert failure.value.check == "checkout_wiring"
        assert state["writes"] == []
    assert state["checkout_upstream"] == []
    assert state["catalog_reads"] == 1
    assert state["rows"] == before
