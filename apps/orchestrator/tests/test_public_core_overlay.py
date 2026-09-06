"""Trusted overlay must load shipped templates, not a directory relative to cwd."""

import io
import tarfile
from pathlib import Path

import pytest
from docker.errors import NotFound

from omnia_orchestrator.services.machine_business_config import apply_public_core_overlay


@pytest.mark.parametrize("canonical_cwd", [True, False])
def test_public_overlay_uploads_real_shipped_webhook_from_any_cwd(
    monkeypatch, tmp_path, canonical_cwd
):
    orchestrator = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(orchestrator if canonical_cwd else tmp_path)
    relative = "src/app/api/max/webhook/route.ts"
    expected = (orchestrator / "templates/max-miniapp-nextjs" / relative).read_bytes()
    uploads = []

    class Core:
        def get_archive(self, _path):
            raise NotFound("file absent")

        def put_archive(self, destination, archive):
            uploads.append((destination, archive))
            return True

    apply_public_core_overlay(Core())

    assert len(uploads) == 1
    destination, archive = uploads[0]
    assert destination == "/app"
    with tarfile.open(fileobj=io.BytesIO(archive)) as uploaded:
        assert set(uploaded.getnames()) == {
            relative,
            "src/app/api/omnia/integrations/[...path]/route.ts",
            "src/app/api/max/session/route.ts",
            "src/lib/max/session.ts",
        }
        proxy = uploaded.extractfile("src/app/api/omnia/integrations/[...path]/route.ts")
        assert proxy is not None
        assert (
            proxy.read()
            == (
                orchestrator
                / "templates/max-miniapp-nextjs/src/app/api/omnia/integrations/[...path]/route.ts"
            ).read_bytes()
        )
        for session_path in ("src/app/api/max/session/route.ts", "src/lib/max/session.ts"):
            session_source = uploaded.extractfile(session_path)
            assert session_source is not None
            assert session_source.read() == (
                orchestrator / "templates/max-miniapp-nextjs" / session_path
            ).read_bytes()
        entry = uploaded.getmember(relative)
        assert (entry.mode, entry.uid, entry.gid) == (0o644, 1000, 1000)
        contents = uploaded.extractfile(entry)
        assert contents is not None
        assert contents.read() == expected
