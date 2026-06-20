"""Tests for GitHub repo import: parse_github_url, tarball_to_files, detect_template."""
import io
import tarfile

import pytest

from omnia_api.services.repo_import import detect_template, parse_github_url, tarball_to_files


def _make_tar(files: dict[str, bytes]) -> bytes:
    """Build a .tar.gz with all files wrapped under a 'wrap/' top-level dir,
    mirroring what GitHub's tarball API returns (owner-repo-sha/<rel>)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for rel, data in files.items():
            info = tarfile.TarInfo(name=f"wrap/{rel}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# parse_github_url
# ---------------------------------------------------------------------------


def test_parse_github_url_full_https():
    assert parse_github_url("https://github.com/owner/repo") == ("owner", "repo")


def test_parse_github_url_dot_git():
    assert parse_github_url("https://github.com/owner/repo.git") == ("owner", "repo")


def test_parse_github_url_shorthand():
    assert parse_github_url("owner/repo") == ("owner", "repo")


def test_parse_github_url_trailing_slash():
    assert parse_github_url("https://github.com/owner/repo/") == ("owner", "repo")


def test_parse_github_url_rejects_gitlab():
    with pytest.raises(ValueError):
        parse_github_url("https://gitlab.com/a/b")


def test_parse_github_url_rejects_bare_string():
    with pytest.raises(ValueError):
        parse_github_url("notaurl")


# ---------------------------------------------------------------------------
# tarball_to_files — extraction, skipping, binary detection
# ---------------------------------------------------------------------------


def test_strips_wrapper_and_skips_binaries_and_git():
    tar = _make_tar(
        {
            "index.html": b"<h1>hi</h1>",
            ".git/config": b"[core]\n\trepositoryformatversion = 0\n",
            "logo.png": b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x01",
        }
    )
    r = tarball_to_files(tar)
    assert "index.html" in r.files
    assert ".git/config" not in r.files  # .git dir stripped
    assert "logo.png" not in r.files  # binary dropped
    assert r.skipped_binaries == 1
    assert r.template == "blank"  # static index.html → blank


def test_skips_node_modules():
    tar = _make_tar(
        {
            "index.html": b"<h1>ok</h1>",
            "node_modules/react/index.js": b"module.exports={}",
        }
    )
    r = tarball_to_files(tar)
    assert "index.html" in r.files
    assert "node_modules/react/index.js" not in r.files


def test_skips_oversized_file(monkeypatch):
    import omnia_api.services.repo as repo_svc

    monkeypatch.setattr(repo_svc, "MAX_FILE_BYTES", 10)
    tar = _make_tar({"big.txt": b"x" * 20, "small.txt": b"y" * 5})
    # Re-import after monkeypatch — tarball_to_files reads MAX_FILE_BYTES at call time
    from omnia_api.services import repo_import as ri_mod
    import importlib
    importlib.reload(ri_mod)
    r = ri_mod.tarball_to_files(tar)
    assert "small.txt" in r.files
    assert "big.txt" not in r.files


# ---------------------------------------------------------------------------
# detect_template
# ---------------------------------------------------------------------------


def test_detect_code_for_non_static():
    assert detect_template({"main.py": "print(1)"}) == "code"


def test_detect_blank_for_static_index():
    assert detect_template({"index.html": "<html></html>"}) == "blank"


def test_detect_code_when_package_json_present():
    # index.html exists but package.json is also present → JS project, not bare static
    assert detect_template({"package.json": "{}", "index.html": "<x>"}) == "code"


def test_detect_code_for_empty():
    assert detect_template({}) == "code"


# ---------------------------------------------------------------------------
# truncation
# ---------------------------------------------------------------------------


def test_truncation():
    files = {f"f{i}.txt": b"x" for i in range(150)}
    r = tarball_to_files(_make_tar(files))
    assert r.truncated is True
    assert len(r.files) == 100
