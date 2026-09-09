import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_bridge_javascript_parses_without_signing_credentials(tmp_path):
    from omnia_orchestrator.services.restoration_node_bridge import NODE_BRIDGE_SOURCE

    node = os.environ.get("NODE_BRIDGE_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node runtime unavailable")
    path = tmp_path / "bridge.cjs"
    path.write_text(NODE_BRIDGE_SOURCE, encoding="utf-8")
    result = subprocess.run(
        [node, "--check", str(path)], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
    assert "token_secret" not in NODE_BRIDGE_SOURCE


def test_real_pg_client_pool_actor_transport(tmp_path):
    """Actual pg8 Client/Pool over HTTP and disposable PostgreSQL; never production."""
    if not os.environ.get("NODE_BRIDGE_DB_URL"):
        pytest.skip("Explicit disposable PostgreSQL NODE_BRIDGE_DB_URL required")
    from omnia_orchestrator.services.restoration_node_bridge import NODE_BRIDGE_SOURCE

    node = os.environ.get("NODE_BRIDGE_NODE") or shutil.which("node")
    assert node, "Node runtime required for bridge acceptance"
    bridge = tmp_path / "bridge.cjs"
    bridge.write_text(NODE_BRIDGE_SOURCE, encoding="utf-8")
    fixture = Path(__file__).parent / "fixtures" / "restoration_node_bridge.cjs"
    result = subprocess.run(
        [node, "--require", str(bridge), str(fixture)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert '"passed":12' in result.stdout
