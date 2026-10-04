"""The real public-core overlay repairs an old tree before route imports run."""

import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path
from uuid import uuid4

from docker.errors import NotFound

from yleum_orchestrator.services.machine_business_config import apply_public_core_overlay

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "apps/orchestrator/templates/max-miniapp-nextjs"
HARNESS = ROOT / "apps/api/tests/fixtures/max_analytics_core_harness.cjs"
LEGACY = json.loads((Path(__file__).parent / "fixtures/max_analytics_legacy_core.json").read_text())


def test_actual_overlay_delivers_new_route_import_closure_in_one_archive(tmp_path):
    shutil.copytree(TEMPLATE / "src", tmp_path / "src")
    helper = "src/lib/omnia/analytics.ts"
    (tmp_path / helper).unlink()
    routes = (
        "src/app/api/max/session/route.ts",
        "src/app/api/omnia/actions/route.ts",
        "src/app/api/omnia/events/route.ts",
    )
    for relative in routes:
        (tmp_path / relative).write_text(LEGACY["files"][relative])
    product = tmp_path / "src/app/page.tsx"
    product.write_text("export default function Coffee(){return 'Coffee';}")
    uploads = []

    class Core:
        def get_archive(self, path):
            file = tmp_path / path.removeprefix("/app/")
            if not file.exists():
                raise NotFound("old tree has no helper")
            data = file.read_bytes()
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w") as output:
                entry = tarfile.TarInfo(file.name)
                entry.size = len(data)
                output.addfile(entry, io.BytesIO(data))
            return iter([archive.getvalue()]), {}

        def put_archive(self, destination, data):
            assert destination == "/app"
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                uploads.append(set(archive.getnames()))
                for entry in archive:
                    target = tmp_path / entry.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extractfile(entry).read())
            return True

    core = Core()
    apply_public_core_overlay(core)
    assert uploads == [set(routes) | {helper}]
    apply_public_core_overlay(core)
    assert len(uploads) == 1
    assert product.read_text().endswith("'Coffee';}")
    for route in ("max/session", "omnia/actions", "omnia/events"):
        result = subprocess.run(
            ["node", str(HARNESS)],
            input=json.dumps(
                {
                    "project": str(uuid4()),
                    "tree": str(tmp_path / "src"),
                    "route": route,
                    "body": {"initData": "forged"},
                    "actor": None,
                    "public": True,
                }
            ),
            text=True,
            capture_output=True,
            check=True,
            timeout=15,
        )
        data = json.loads(result.stdout)
        # Real Node loads the current routes, new helper and old retained auth
        # dependencies. Rejected requests never emit an open or action.
        assert data["status"] == 401 and data["sent"] == []
