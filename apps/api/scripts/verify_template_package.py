"""Verify bundled scaffolds from an installed wheel or the built API image.

Run outside the checkout. Only repository object storage is replaced, with local
tar archives; materialization, Git commits/readback and export assembly are real.
No settings, database, queue, model or external storage connection is required.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4


def file_hashes(root: Path) -> dict[str, str]:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise AssertionError("materialized output must not contain symlinks")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def content_hashes(files: dict[str, str | bytes]) -> dict[str, str]:
    return {
        name: hashlib.sha256(content.encode() if isinstance(content, str) else content).hexdigest()
        for name, content in files.items()
    }


def verify(manifest_path: Path, package_root: Path) -> None:
    import pygit2

    import omnia_api
    from omnia_api.services import repo
    from omnia_api.services.project_export import build_runnable_export, read_template_tree
    from omnia_api.services.template_materialization import TEMPLATES, materialize_template

    actual_package = Path(omnia_api.__file__).resolve().parent
    expected_package = package_root.resolve() / "omnia_api"
    if actual_package != expected_package:
        raise AssertionError("omnia_api was not imported from the expected installed package")
    if TEMPLATES.resolve() != actual_package / "templates":
        raise AssertionError("templates must belong to the verified installed package")
    golden = json.loads(manifest_path.read_text(encoding="utf-8"))["templates"]
    if set(golden) != {"blank", "landing", "portfolio", "blog", "fullstack"}:
        raise AssertionError("golden must contain all five bundled project templates")
    # This consumer reads assets at import time; a correct materializer alone
    # cannot prove the packaged API still starts with its public asset routes.
    from omnia_api.routers.public import _KIT_ASSETS

    for name in ("omnia-kit.css", "omnia-kit.js", "anime.min.js"):
        if hashlib.sha256(_KIT_ASSETS[name]).hexdigest() != golden["blank"][
            f"assets/{name}"
        ]["sha256"]:
            raise AssertionError(f"public kit consumer differs from golden: {name}")
    print("Public router import and three kit asset hashes passed.")
    archives: dict[UUID, bytes] = {}

    def upload(project_id: UUID, source: Path) -> None:
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            archive.add(source, arcname=".")
        archives[project_id] = stream.getvalue()

    def download(project_id: UUID, destination: Path) -> bool:
        payload = archives.get(project_id)
        if payload is None:
            return False
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            archive.extractall(destination, filter="data")
        return True

    with (
        tempfile.TemporaryDirectory(prefix="omnia-template-package-") as temporary,
        patch.object(repo, "_upload", upload),
        patch.object(repo, "_try_download", download),
    ):
        root = Path(temporary)
        for name, entries in golden.items():
            expected = {path: entry["sha256"] for path, entry in entries.items()}
            destination = root / name
            materialize_template(TEMPLATES / name, destination)
            if file_hashes(destination) != expected:
                raise AssertionError(f"{name}: standalone materialization differs from golden")

            project_id = uuid4()
            commit = repo.init_repo(project_id, TEMPLATES / name, name)
            files = repo.read_files(project_id, commit)
            if content_hashes(files) != expected:
                raise AssertionError(f"{name}: initial Git commit differs from golden")
            with repo._open_workdir(project_id, must_exist=True) as workdir:
                repository = pygit2.Repository(str(workdir))
                tree = repository[commit].tree
                if any(f"{tree[path].filemode:o}" != entry["mode"]
                       for path, entry in entries.items()):
                    raise AssertionError(f"{name}: committed file modes differ from golden")

            # Export consumes a standalone scaffold, not the platform's source
            # tree. Generated/committed files still win over its bundled copy.
            if content_hashes(read_template_tree(destination)) != expected:
                raise AssertionError(f"{name}: standalone export reader lost template files")
            exported = build_runnable_export(name, files, templates_root=root)
            exported.pop("README.omnia.md", None)
            if content_hashes(exported) != expected:
                raise AssertionError(f"{name}: runnable export changed committed template files")
            print(f"{name}: materialize, Git bytes/modes, export passed ({len(expected)} files)")
    print("Installed package smoke passed: 5 templates; no DB or external storage used.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="absolute path to the frozen golden JSON")
    parser.add_argument("--package-root", required=True, type=Path,
                        help="directory containing the expected installed omnia_api package")
    arguments = parser.parse_args()
    if not arguments.manifest.is_absolute() or not arguments.package_root.is_absolute():
        parser.error("manifest and package-root must be absolute paths")
    verify(arguments.manifest, arguments.package_root)
