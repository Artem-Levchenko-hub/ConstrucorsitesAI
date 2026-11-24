import base64
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from yleum_orchestrator.services import machine_npm_seed as seed


def sri(data):
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


def starter(tmp_path, *, extra=None):
    packages = {"demo@1.0.0": {"resolution": {"integrity": sri(b"demo")}}}
    if extra:
        packages.update(extra)
    files = {
        seed.LOCK: {"packages": packages},
        seed.FONTS: {"fonts": {"test": {"package": "@fontsource/test", "version": "2.0.0",
                                        "integrity": sri(b"font")}}},
    }
    for filename, value in files.items():
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
    return tmp_path


def test_committed_seed_includes_linux_native_development_and_catalog():
    archives = seed._archives((seed.REPO / seed.LOCK).read_bytes(),
                              (seed.REPO / seed.FONTS).read_bytes(), "x64")
    for prefix in ("/react/-/", "/next/-/", "/typescript/-/", "/undici-types/-/",
                   "/@next/swc-linux-x64-gnu/-/", "/@fontsource/onest/-/"):
        assert any(name.startswith(prefix) for name in archives), prefix
    assert not any("swc-win32" in name for name in archives)


def test_prepare_publishes_only_complete_seed_and_reuses_integrity_objects(tmp_path, monkeypatch):
    repo, root = starter(tmp_path / "repo"), tmp_path / "cache"
    monkeypatch.setattr(seed, "REPO", repo)
    monkeypatch.setattr(seed, "CACHE_ROOT", root)
    monkeypatch.setattr(seed, "_cpu", lambda: "x64")
    assert seed.npm_seed_directory() is None

    def download(path, dest, expected):
        data = b"font" if path.startswith("/@fontsource") else b"demo"
        assert hashlib.sha512(data).hexdigest() == expected
        dest.write_bytes(data)

    monkeypatch.setattr(seed, "_download", download)
    result = seed.prepare(repo, root, "x64")
    directory = seed.npm_seed_directory()
    assert directory == Path(result["directory"])
    assert result["downloaded"] == result["archives"] == 2
    assert directory is not None
    archive = hashlib.sha256(b"/demo/-/demo-1.0.0.tgz").hexdigest() + ".tgz"
    obj = root / "objects" / hashlib.sha512(b"demo").hexdigest()
    assert (directory / archive).samefile(obj)
    monkeypatch.setattr(seed, "_download", lambda *args: pytest.fail("unexpected download"))
    assert seed.prepare(repo, root, "x64")["downloaded"] == 0

    starter(repo, extra={"new@1.0.0": {"resolution": {"integrity": sri(b"new")}}})

    def unavailable(*args):
        raise OSError("temporary network failure")

    monkeypatch.setattr(seed, "_download", unavailable)
    with pytest.raises(OSError, match="temporary network failure"):
        seed.prepare(repo, root, "x64")
    assert seed.npm_seed_directory() is None
    assert (directory / archive).read_bytes() == b"demo"
    monkeypatch.setattr(seed, "_download", lambda path, dest, expected: dest.write_bytes(b"new"))
    newer = seed.prepare(repo, root, "x64")
    assert newer["downloaded"] == 1
    assert (Path(newer["directory"]) / archive).samefile(obj)


@pytest.mark.parametrize(("status", "payload", "error"), [
    (200, b"truncated", ValueError),
    (302, b"redirected", OSError),
])
def test_fetch_refuses_mismatched_bytes_and_redirects(
    tmp_path, monkeypatch, status, payload, error,
):
    https = MagicMock()
    response = https.return_value.getresponse.return_value.__enter__.return_value
    response.status = status
    response.read.side_effect = [payload, b""]
    monkeypatch.setattr(seed.http.client, "HTTPSConnection", https)
    path = "/demo/-/demo-1.0.0.tgz"
    with pytest.raises(error):
        seed._download(path, tmp_path / "out", hashlib.sha512(b"expected").hexdigest())
    assert https.call_args.args == ("registry.npmjs.org",)
    assert https.return_value.request.call_count == 1
    https.return_value.close.assert_called_once()
