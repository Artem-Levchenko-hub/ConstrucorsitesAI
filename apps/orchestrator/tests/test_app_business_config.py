import io
import json
import tarfile
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from tests.test_project_machine_manifest import payload
from yleum_orchestrator.core.project_machine import MachineManifest
from yleum_orchestrator.services import app_business_config as config


def test_old_manifest_nested_serialization_and_digest_stay_unchanged():
    manifest = MachineManifest.model_validate(payload())
    old = manifest.model_dump(mode="json")
    assert "app_config_protocol" not in old
    class Request(BaseModel):
        manifest: MachineManifest
    assert Request(manifest=manifest).model_dump(mode="json")["manifest"] == old
    assert json.loads(manifest.canonical_json()) == old
    opted_in = MachineManifest.model_validate({**payload(), "app_config_protocol": 1})
    assert opted_in.digest() != manifest.digest()
    assert opted_in.model_dump()["app_config_protocol"] == 1
    with pytest.raises(ValidationError):
        MachineManifest.model_validate({**payload(), "app_config_protocol": 2})
    with pytest.raises(ValidationError):
        MachineManifest.model_validate({**payload(), "app_config_protocol": True})


def test_first_publication_accepts_authoritative_default_config_version_zero():
    from tests.test_cell_publication import request

    assert request(business_config_version=0).business_config_version == 0


def test_envelope_is_bounded_ascii_public_data_and_digest_is_order_independent():
    business = {"content": [{"id": "tea", "title": "Чай"}], "app_name": "Shop"}
    raw = config.app_config_envelope(business, 3)
    assert raw.isascii()
    assert json.loads(raw) == {"config_version": 3, "config": business}
    assert config.config_digest(business) == config.config_digest(dict(reversed(business.items())))
    with pytest.raises(ValueError):
        config.app_config_envelope({"summary": "я" * config.MAX_APP_CONFIG_BYTES}, 1)
    with pytest.raises(ValueError, match="public"):
        config.app_config_envelope({"AUTH_SECRET": "must-never-be-mounted"}, 1)


def test_atomic_updates_do_not_replace_directory_or_leave_partial_files(tmp_path):
    directory = tmp_path / "public-data"
    config.write_app_config(directory, {"content": []}, 1)
    identity = directory.stat().st_ino
    config.write_app_config(directory, {"content": [{"id": "tea"}]}, 2)
    assert directory.stat().st_ino == identity
    assert [path.name for path in directory.iterdir()] == ["business.json"]
    assert json.loads((directory / "business.json").read_bytes())["config_version"] == 2


@pytest.mark.parametrize("invalid", [False, "corrupt", "oversize", "link"])
def test_actual_app_file_reader_is_fixed_bounded_and_uses_own_timeout(invalid):
    business = {"content": []}
    raw = config.app_config_envelope(business, 7)
    if invalid == "corrupt":
        raw = b"{"
    elif invalid == "oversize":
        raw = b"x" * (config.MAX_APP_CONFIG_BYTES + 1)
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as output:
        entry = tarfile.TarInfo("business.json")
        entry.size = len(raw)
        if invalid == "link":
            entry.type = tarfile.SYMTYPE
            entry.linkname = "/private"
            entry.size = 0
        output.addfile(entry, io.BytesIO(raw))
    calls = []
    class API:
        timeout = 60
        def get_archive(self, container, path, chunk_size):
            calls.append((container, path, chunk_size, self.timeout))
            return iter([archive.getvalue()]), {}
    api = API()
    app = SimpleNamespace(id="owned", client=SimpleNamespace(api=api))
    assert config.app_file_matches(app, business, 7) is (invalid is False)
    assert calls == [("owned", config.APP_CONFIG_PATH, 65536, 3)]
    assert api.timeout == 60


def test_docker_opt_in_mount_is_read_only_and_never_captured(tmp_path, monkeypatch):
    from tests.test_docker_machine_backend import backend

    runtime = backend(tmp_path)
    monkeypatch.setattr(runtime, "project_database_env", lambda: {})
    old = MachineManifest.model_validate(payload())
    old_options = runtime.container_options(old, "namespace", 1)
    assert "YLEUM_APP_CONFIG_PATH" not in old_options["environment"]
    assert all(mount["bind"] != config.APP_CONFIG_DIRECTORY
               for mount in old_options["volumes"].values())
    manifest = MachineManifest.model_validate({**payload(), "app_config_protocol": 1})
    options = runtime.container_options(manifest, "namespace", 1)
    directory = str(runtime.app_config_directory)
    assert options["volumes"][directory] == {"bind": config.APP_CONFIG_DIRECTORY, "mode": "ro"}
    assert options["environment"]["YLEUM_APP_CONFIG_PATH"] == config.APP_CONFIG_PATH
    assert directory not in runtime.volume_mapping(manifest)
    config.write_app_config(runtime.app_config_directory, {"content": []}, 2)
    first = options
    config.write_app_config(runtime.app_config_directory, {"content": [{"id": "new"}]}, 3)
    assert runtime.container_options(manifest, "namespace", 1) == first


def test_docker_prepares_authoritative_config_before_first_build_or_service(tmp_path, monkeypatch):
    from tests.test_docker_machine_backend import backend

    runtime = backend(tmp_path)
    monkeypatch.setattr(runtime, "project_database_env", lambda: {})
    canonical = tmp_path / str(runtime.workspace_id) / "business-config.json"
    canonical.parent.mkdir()
    canonical.write_text(json.dumps({
        "project_id": str(runtime.project_id), "owner_id": str(runtime.owner_id),
        "version": 0, "config": {"app_name": "Shop", "content": []},
    }))
    opted = MachineManifest.model_validate({**payload(), "app_config_protocol": 1})
    runtime.container_options(opted, "namespace", 1)
    assert json.loads((runtime.app_config_directory / "business.json").read_bytes()) == {
        "config_version": 0, "config": {"app_name": "Shop", "content": []},
    }
