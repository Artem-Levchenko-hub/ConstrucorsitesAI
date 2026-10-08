"""Fixed platform scripts remain byte-pinned and origin-bound in the browser."""

import hashlib
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from yleum_api.services import max_behavior_browser as browser
from yleum_api.services import max_behavior_proof as proof

from . import test_max_behavior_browser as browser_fixture
from .behavior_browser_fixture import installed_chromium
from .test_max_behavior_browser import adapter, fixture_files
from .test_max_behavior_proof import THEME, assets, binding

local_fixture = browser_fixture.local_fixture

PATHS = (
    "/omnia-inspector.js",
    "/_omnia/inspector.js",
    "/omnia-remix-cta.js",
    "/omnia-brief-narration.js",
)
SDK = "https://st.max.ru/js/max-web-app.js"


def pinned(path, body=b"void 0;"):
    return proof.ObservedAsset(path, hashlib.sha256(body).hexdigest(), len(body))


def test_compiled_accepts_all_five_exact_platform_locations():
    bound = binding()
    witness = replace(assets(bound), platform_assets=tuple(map(pinned, (*PATHS, SDK))))
    assert proof._compiled(witness, bound) is witness


@pytest.mark.parametrize(
    "bad",
    [
        pinned("/extra.js"),
        pinned(PATHS[0] + "?v=1"),
        pinned("https://foreign.example" + PATHS[0]),
        pinned("/x/../omnia-inspector.js"),
        pinned("//st.max.ru/js/max-web-app.js"),
        replace(pinned(PATHS[0]), sha256="bad"),
        replace(pinned(PATHS[0]), bytes=0),
        replace(pinned(PATHS[0]), bytes=True),
        replace(pinned(PATHS[0]), bytes=8388609),
    ],
)
def test_platform_metadata_rejects_unapproved_locations_and_invalid_pins(bad):
    bound = binding()
    with pytest.raises(proof.BehaviorProofError, match="BEHAVIOR_PLATFORM_ASSETS_INVALID"):
        proof._compiled(replace(assets(bound), platform_assets=(bad,)), bound)


@pytest.mark.parametrize("locations", [(PATHS[0], PATHS[0]), (*PATHS, SDK, SDK)])
def test_duplicate_or_excess_platform_assets_rejected(locations):
    bound = binding()
    with pytest.raises(proof.BehaviorProofError, match="BEHAVIOR_PLATFORM_ASSETS_INVALID"):
        proof._compiled(
            replace(assets(bound), platform_assets=tuple(map(pinned, locations))), bound
        )


def test_platform_script_cannot_be_disguised_as_compiled_chunk():
    bound = binding()
    with pytest.raises(proof.BehaviorProofError, match="BEHAVIOR_COMPILED_ASSETS_INVALID"):
        proof._compiled(replace(assets(bound), assets=(pinned(PATHS[0]),)), bound)


@pytest.mark.parametrize("mutation", ["", "bytes", "size", "query", "foreign", "redirect"])
def test_browser_only_serves_exact_relative_platform_pins(local_fixture, monkeypatch, mutation):
    # Bind this isolated route test to the executed fixture, including Windows
    # checkout line endings. The frozen production probe has its own exact test.
    monkeypatch.setattr(browser, "PROBE_SHA256", browser.executed_probe_digest())
    local_fixture.files = fixture_files("")
    origin = f"http://127.0.0.1:{local_fixture.server_port}"
    # This regression exercises asset admission while retaining real painted
    # interactions and reloads at both viewports. Density has separate coverage.
    bound_contract = proof.required_contract(THEME, template="max_miniapp")
    bound = binding(bound_contract)
    request = proof.BehaviorDriverInput(bound, bound_contract, proof.PrivatePreviewCapability(NS()))
    compiled = tuple(pinned(p, body) for p, body in local_fixture.files.items() if p != "/")
    platform = tuple(map(pinned, PATHS))
    for path in PATHS:
        local_fixture.files[path] = b"void 0;"
    requested = list(PATHS)
    if mutation == "bytes":
        local_fixture.files[PATHS[0]] = b"void 1;"
    elif mutation == "size":
        local_fixture.files[PATHS[0]] += b" "
    elif mutation == "query":
        requested[0] += "?v=1"
    elif mutation == "foreign":
        requested[0] = "http://localhost:" + str(local_fixture.server_port) + PATHS[0]
    elif mutation == "redirect":
        handler = local_fixture.RequestHandlerClass
        original = handler.do_GET

        def redirected(self):
            if self.path == PATHS[0]:
                self.send_response(302)
                self.send_header("Location", PATHS[1])
                self.end_headers()
            else:
                original(self)

        monkeypatch.setattr(handler, "do_GET", redirected)
    scripts = "".join(f'<script src="{path}"></script>' for path in requested).encode()
    local_fixture.files["/"] = local_fixture.files["/"].replace(b"</body>", scripts + b"</body>")
    witness = proof.CompiledAssetWitness(
        bound, compiled, "f" * 64, "served_candidate_compilation_v1", platform
    )
    arguments = dict(
        origin=origin,
        bootstrap=origin + "/_fixture/bootstrap",
        adapter=adapter(),
        executable_path=installed_chromium(),
        launch_args=("--no-sandbox",),
    )
    if mutation:
        with pytest.raises(proof.BehaviorProofError, match="BEHAVIOR_OBSERVATION_UNVERIFIED"):
            browser._execute_browser(request, witness, True, **arguments)
    else:
        result = browser._execute_browser(request, witness, True, **arguments)
        assert result.status == "PASS_OBSERVED"
    assert local_fixture.mutations == 0
