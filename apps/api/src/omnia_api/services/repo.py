"""Bare-repo storage поверх MinIO + pygit2.

Каждому проекту соответствует один tar.gz в bucket `projects` под ключом
`repos/{project_id}.tar.gz`.
При операции tarball распаковывается во временную папку, pygit2 работает как с обычным репо,
обратно упаковывается и заливается. Простая реализация — оптимизация (хранить только .git/objects)
оставлена на потом, когда будет реальная нагрузка.
"""

from __future__ import annotations

import shutil
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import pygit2
from minio.commonconfig import CopySource
from minio.error import S3Error

from omnia_api.core.config import get_settings
from omnia_api.core.minio import get_minio_client

MAX_FILES = 100
MAX_FILE_BYTES = 2 * 1024 * 1024
SIGNATURE = ("Omnia AI", "ai@omnia.ai")


def _repo_key(project_id: UUID) -> str:
    return f"repos/{project_id}.tar.gz"


def _bucket() -> str:
    return get_settings().minio_bucket_projects


def _signature() -> pygit2.Signature:
    return pygit2.Signature(*SIGNATURE)


def _try_download(project_id: UUID, dest: Path) -> bool:
    client = get_minio_client()
    tar_path = dest.parent / f"{dest.name}.tar.gz"
    try:
        client.fget_object(_bucket(), _repo_key(project_id), str(tar_path))
    except S3Error as e:
        if e.code in {"NoSuchKey", "NoSuchBucket"}:
            return False
        raise
    with tarfile.open(tar_path, "r:gz") as tar:
        tar.extractall(dest)
    tar_path.unlink(missing_ok=True)
    return True


def _upload(project_id: UUID, src: Path) -> None:
    client = get_minio_client()
    tar_path = src.parent / f"{src.name}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(src, arcname=".")
    client.fput_object(_bucket(), _repo_key(project_id), str(tar_path))
    tar_path.unlink(missing_ok=True)


@contextmanager
def _open_workdir(project_id: UUID, must_exist: bool) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix=f"omnia-{project_id}-") as tmp:
        workdir = Path(tmp) / "repo"
        workdir.mkdir()
        existed = _try_download(project_id, workdir)
        if must_exist and not existed:
            raise RuntimeError(f"repo for project {project_id} not found in MinIO")
        yield workdir


def init_repo(project_id: UUID, template_dir: Path, template_name: str) -> str:
    with tempfile.TemporaryDirectory(prefix=f"omnia-init-{project_id}-") as tmp:
        workdir = Path(tmp) / "repo"
        workdir.mkdir()
        if template_dir.exists():
            shutil.copytree(template_dir, workdir, dirs_exist_ok=True)
        repo = pygit2.init_repository(str(workdir), bare=False)
        sig = _signature()
        index = repo.index
        for path in sorted(workdir.rglob("*")):
            if path.is_dir():
                continue
            if ".git" in path.parts:
                continue
            rel = path.relative_to(workdir).as_posix()
            blob_oid = repo.create_blob(path.read_bytes())
            index.add(pygit2.IndexEntry(rel, blob_oid, pygit2.GIT_FILEMODE_BLOB))
        index.write()
        tree_oid = index.write_tree()
        commit_oid = repo.create_commit(
            "HEAD", sig, sig, f"Initial: {template_name}", tree_oid, []
        )
        _upload(project_id, workdir)
        return str(commit_oid)


def init_from_files(project_id: UUID, files: dict[str, str], message: str) -> str:
    """Seed a fresh project repo from a files dict (used by GitHub import).

    Same MinIO tarball storage + pygit2 flow as init_repo, but instead of
    copying a template directory we write each file directly as a blob.
    This lets the caller supply any arbitrary set of text files (e.g. from a
    GitHub tarball) without touching the filesystem first.

    Returns the hex SHA of the initial commit.
    """
    with tempfile.TemporaryDirectory(prefix=f"omnia-import-{project_id}-") as tmp:
        workdir = Path(tmp) / "repo"
        workdir.mkdir()
        repo = pygit2.init_repository(str(workdir), bare=False)
        sig = _signature()
        index = repo.index
        for rel, content in sorted(files.items()):
            blob_oid = repo.create_blob(content.encode("utf-8"))
            index.add(pygit2.IndexEntry(rel, blob_oid, pygit2.GIT_FILEMODE_BLOB))
        index.write()
        tree_oid = index.write_tree()
        commit_oid = repo.create_commit(
            "HEAD", sig, sig, message, tree_oid, []
        )
        _upload(project_id, workdir)
        return str(commit_oid)


def duplicate_repo(source_id: UUID, dest_id: UUID) -> None:
    """Deep-copy the source project's bare-repo tarball onto the fork's own key.

    Backs the V4.1b "Remix this" fork: a server-side MinIO copy gives the fork a
    fully isolated repo object. A later ``commit_files`` on the fork re-uploads
    only the fork's key, so the source's bytes (its whole git history) stay
    byte-identical — the isolation invariant. Raises if the source repo is
    absent (forking a project that never got a repo is a real error).
    """
    client = get_minio_client()
    try:
        client.copy_object(
            _bucket(),
            _repo_key(dest_id),
            CopySource(_bucket(), _repo_key(source_id)),
        )
    except S3Error as e:
        if e.code in {"NoSuchKey", "NoSuchBucket"}:
            raise RuntimeError(
                f"repo for source project {source_id} not found in MinIO"
            ) from e
        raise


def delete_repo(project_id: UUID) -> None:
    """Remove the project's bare-repo tarball from MinIO. Idempotent: a missing
    object (already deleted, or never created) is a no-op."""
    client = get_minio_client()
    try:
        client.remove_object(_bucket(), _repo_key(project_id))
    except S3Error as e:
        if e.code in {"NoSuchKey", "NoSuchBucket"}:
            return
        raise


def commit_files(
    project_id: UUID,
    files: dict[str, str],
    message: str,
    parent_sha: str | None = None,
) -> str:
    if len(files) > MAX_FILES:
        raise ValueError(f"too many files: {len(files)} > {MAX_FILES}")
    for path, content in files.items():
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError(f"file {path} exceeds {MAX_FILE_BYTES} bytes")

    with _open_workdir(project_id, must_exist=True) as workdir:
        repo = pygit2.Repository(str(workdir))
        index = repo.index
        for path, content in files.items():
            full = workdir / path
            if content == "":
                if full.exists():
                    full.unlink()
                try:
                    index.remove(path)
                except (KeyError, OSError):
                    # Not in the index (pygit2 raises KeyError or, depending on
                    # build, OSError "index does not contain <path> at stage 0").
                    # Nothing to delete — a no-op, never fatal to the commit.
                    pass
                continue
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
            blob_oid = repo.create_blob(content.encode("utf-8"))
            index.add(pygit2.IndexEntry(path, blob_oid, pygit2.GIT_FILEMODE_BLOB))
        index.write()
        tree_oid = index.write_tree()
        sig = _signature()
        if parent_sha:
            parents = [pygit2.Oid(hex=parent_sha)]
        elif not repo.is_empty:
            parents = [repo.head.target]
        else:
            parents = []
        # ref=None creates a detached commit object without moving HEAD. The
        # app tracks state by commit_sha (oid), never via HEAD — so this is
        # safe, AND it avoids pygit2's "current tip is not the first parent"
        # error when two generations on the same project commit concurrently
        # off the same parent (e.g. user fires a second prompt before the first
        # finishes; freeform's render+vision widens that window).
        commit_oid = repo.create_commit(None, sig, sig, message, tree_oid, parents)
        _upload(project_id, workdir)
        return str(commit_oid)


def read_files(project_id: UUID, commit_sha: str) -> dict[str, str]:
    with _open_workdir(project_id, must_exist=True) as workdir:
        repo = pygit2.Repository(str(workdir))
        commit = repo.get(commit_sha)
        if commit is None:
            raise ValueError(f"commit {commit_sha} not found")
        out: dict[str, str] = {}
        _walk(repo, commit.tree, "", out)
        if len(out) > MAX_FILES:
            raise ValueError(f"too many files in commit: {len(out)} > {MAX_FILES}")
        return out


def read_file(project_id: UUID, commit_sha: str, path: str) -> bytes | None:
    """Возвращает bytes указанного файла из коммита, либо None если нет."""
    with _open_workdir(project_id, must_exist=True) as workdir:
        repo = pygit2.Repository(str(workdir))
        commit = repo.get(commit_sha)
        if commit is None:
            return None
        try:
            entry = commit.tree[path]
        except KeyError:
            return None
        blob = repo[entry.id]
        return bytes(blob.data)


def checkout(project_id: UUID, target_commit_sha: str) -> str:
    """Rollback: создаёт новый коммит, чьё дерево взято из target_commit_sha;
    родитель — текущий HEAD. Старая история не теряется."""
    with _open_workdir(project_id, must_exist=True) as workdir:
        repo = pygit2.Repository(str(workdir))
        target = repo.get(target_commit_sha)
        if target is None:
            raise ValueError(f"commit {target_commit_sha} not found")
        sig = _signature()
        parents = [repo.head.target] if not repo.is_empty else []
        commit_oid = repo.create_commit(
            "HEAD",
            sig,
            sig,
            f"Rollback to {target_commit_sha[:8]}",
            target.tree.id,
            parents,
        )
        # Synchronize working tree с новым HEAD, чтобы в архив попала актуальная версия.
        repo.checkout_tree(repo.get(commit_oid).tree, strategy=pygit2.GIT_CHECKOUT_FORCE)
        _upload(project_id, workdir)
        return str(commit_oid)


def _walk(repo: pygit2.Repository, tree: pygit2.Tree, prefix: str, out: dict[str, str]) -> None:
    for entry in tree:
        path = f"{prefix}{entry.name}" if prefix else entry.name
        if entry.type_str == "tree":
            _walk(repo, repo[entry.id], f"{path}/", out)
            continue
        if entry.type_str != "blob":
            continue
        blob = repo[entry.id]
        try:
            out[path] = blob.data.decode("utf-8")
        except UnicodeDecodeError:
            continue
