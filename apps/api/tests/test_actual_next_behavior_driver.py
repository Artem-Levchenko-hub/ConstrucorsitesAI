"""Optional actual cached Next compile/serve fixture; never an actual customer app.

Enable only with NEXT_BEHAVIOR_TEST_MODULES pointing to existing pinned cache.
No installs, SDK stubs, database, provider or model calls. Auth is loopback fixture
only; product origin/session validation is replaced exclusively by this test.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import socket
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from urllib.request import urlopen

import pytest

from yleum_api.services import max_behavior_browser as browser
from yleum_api.services import max_behavior_proof as b
from yleum_api.services.behavior_compilation_resolver import compilation_operation_id
from yleum_api.services.behavior_driver_configuration import configured_behavior_driver
from yleum_api.services.orchestrator_client import (
    ProjectCellAgentExecResponse,
    ProjectCellWorkspaceIdentity,
)
from yleum_api.services.project_cell_proofs import ProofIdentity

from .test_max_behavior_proof import binding

CACHE = os.environ.get("NEXT_BEHAVIOR_TEST_MODULES", "")
CHROME = "/workspace/qa-tools/playwright/chromium-1234/chrome-linux64/chrome"
REQUEST = "Добавь кнопку «Проверить заявку» для резюме без отправки данных."


def owned_stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=3)


@pytest.mark.skipif(
    not CACHE, reason="existing pinned Next cache not selected; no download fallback"
)
def test_actual_next_compilation_transport_resolver_private_driver_and_served_race(
    tmp_path, monkeypatch
):
    # Actual platform collector is imported from this isolated source tree only.
    import importlib.util

    collector_path = (
        Path(__file__).parents[2]
        / "orchestrator/src/yleum_orchestrator/services/next_compilation_receipt.py"
    )
    spec = importlib.util.spec_from_file_location("owned_next_collector", collector_path)
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
    project = tmp_path / "owned-next"
    project.mkdir()
    (project / "node_modules").symlink_to(Path(CACHE), target_is_directory=True)
    package = {
        "private": True,
        "scripts": {"build": "next build"},
        "dependencies": {"next": "15.5.24", "react": "18.3.1", "react-dom": "18.3.1"},
    }
    sources = {
        "package.json": json.dumps(package),
        "next.config.js": "module.exports={experimental:{cpus:1},poweredByHeader:false};",
        "app/layout.js": "export default function Layout({children}){return <html><body>{children}</body></html>}",  # noqa: E501
        "app/page.js": """"use client";
import {useState} from 'react';
export default function Page(){
const [name,setName]=useState('');const [summary,setSummary]=useState('');
return <main><form id="form" onSubmit={e=>e.preventDefault()}><label>Name
<input id="name" name="name" value={name} onChange={e=>setName(e.target.value)}/></label>
<button id="check" type="button" style={{minWidth:180,minHeight:48}}
onClick={()=>setSummary('Summary '+name)}>Проверить заявку</button></form>
<section id="summary">{summary}</section></main>}
""",
        "app/fixture/bootstrap/route.js": """export function GET(request){return new Response(null,{status:303,headers:{Location:'/', 'Set-Cookie':'fixture=private; Path=/; HttpOnly'}})}""",  # noqa: E501
    }
    for name, content in sources.items():
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    # Hash fixed source before/after actual compilation, never compiled-as-source.
    source_sha = b.digest(sources)
    environment = dict(os.environ, NEXT_TELEMETRY_DISABLED="1", CI="1")
    binary = Path(CACHE) / "next/dist/bin/next"
    with (tmp_path / "build.log").open("wb") as log:
        build = subprocess.Popen(
            ["node", str(binary), "build"],
            cwd=project,
            env=environment,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            assert build.wait(timeout=90) == 0, (
                "owned fixture Next build failed; inspect bounded private build log"
            )
        finally:
            owned_stop(build)
    assert b.digest({name: (project / name).read_text() for name in sources}) == source_sha
    receipt = collector.collect_next_compilation(str(project))
    assert receipt["declared_next_version"] == "15.5.24"
    assert any(
        asset["path"].startswith("/_next/static/chunks/app/page-") for asset in receipt["assets"]
    )
    contract = b.required_contract(REQUEST, template="max_miniapp")
    initial = replace(
        binding(contract), source_revision=source_sha, build_ref="build/sha256/" + source_sha
    )
    identity = ProofIdentity(
        initial.workspace_id,
        initial.generation_run_id,
        7,
        source_sha,
        "c" * 64,
        "d" * 64,
        "e" * 64,
        "f" * 64,
        "f" * 64,
        "profile-v1",
        "1" * 64,
    )
    bound = replace(initial, proof_key=identity.proof_key)
    op = compilation_operation_id(bound)
    receipt.update(
        workspace_id=str(bound.workspace_id),
        project_id=str(bound.project_id),
        generation_run_id=str(bound.generation_run_id),
        fencing_epoch=7,
        source_revision=source_sha,
        operation_id=str(op),
        manifest_digest=identity.cell_manifest_digest,
        collector_source_sha256=hashlib.sha256(collector_path.read_bytes()).hexdigest(),
    )
    wi = ProjectCellWorkspaceIdentity(source_sha, "c" * 64, "d" * 64, "e" * 64, "f" * 64, "1" * 64)
    response = ProjectCellAgentExecResponse(
        True, 0, "", False, source_sha, op, wi, wi, False, compiled_asset_receipt=receipt
    )
    # Exercise actual strict transport decoder and retained resolver, no command rerun.
    from dataclasses import asdict

    payload = asdict(response)
    payload["operation_id"] = str(op)
    response = ProjectCellAgentExecResponse.from_json(payload)
    handle = NS(
        current_identity=AsyncMock(return_value=identity),
        operation_status=AsyncMock(
            return_value=NS(operation_id=op, state="completed", terminal_response=response)
        ),
    )
    config = NS(
        max_behavior_browser_executable=CHROME,
        max_behavior_browser_sha256=hashlib.sha256(Path(CHROME).read_bytes()).hexdigest(),
        max_behavior_adapter_registry=json.dumps(
            {
                "max-miniapp-nextjs": {
                    "coffee_summary": {
                        "button": "#check",
                        "form": "#form",
                        "text_input": "#name",
                        "summary": "#summary",
                    }
                }
            }
        ),
        max_behavior_vendor_asset_registry="",
    )
    driver = configured_behavior_driver(handle, config)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    monkeypatch.setattr(browser, "_origin", lambda request: (origin, origin + "/fixture/bootstrap"))
    with (tmp_path / "server.log").open("wb") as log:
        server = subprocess.Popen(
            ["node", str(binary), "start", "--hostname", "127.0.0.1", "--port", str(port)],
            cwd=project,
            env=environment,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                try:
                    with urlopen(origin, timeout=1) as result:
                        assert result.status == 200
                    break
                except OSError:
                    assert server.poll() is None and time.monotonic() < deadline
                    time.sleep(0.1)
            proof = asyncio.run(
                b.observe_candidate(bound, contract, driver, b.PrivatePreviewCapability(None))
            )
            assert proof["status"] == "PASS_OBSERVED"
            assert proof["binding"] == bound.to_json()
            assert proof["observed_assets"] == receipt["assets"]
            b.validate_saved_receipt(proof, bound, contract)
            # A real served byte mutation must invalidate the retained compiler receipt.
            asset = next(x for x in receipt["assets"] if "/app/page-" in x["path"])
            (project / ".next" / asset["path"].removeprefix("/_next/")).write_bytes(
                b"window.MUTATED=1;"
            )
            with pytest.raises(b.BehaviorProofError, match="BEHAVIOR_COMPILED_ASSETS_CHANGED"):
                asyncio.run(
                    b.observe_candidate(bound, contract, driver, b.PrivatePreviewCapability(None))
                )
            assert handle.operation_status.await_count >= 3
        finally:
            owned_stop(server)
