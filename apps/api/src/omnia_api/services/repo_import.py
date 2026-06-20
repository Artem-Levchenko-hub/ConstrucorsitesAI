"""Import an external GitHub repo as a project's seed files.

Clones via the GitHub tarball API (public = anon, private = the user's OAuth
token), strips GitHub's top-level wrapper dir, and applies repo.py's size/count
limits — binaries and oversized files are dropped (text-only repos, matching
repo._walk's UTF-8 contract). detect_template maps the files onto an existing
Omnia template: a root index.html (static site) -> 'blank' (gets the /p/ preview);
anything else -> 'code' (the file-only, no-container surface added in migration
0017 — perfect for arbitrary source). source='imported' (set by the caller) is
what actually gates the edit pipeline; this template is only a preview hint.
"""

from __future__ import annotations

import io
import re
import tarfile
from dataclasses import dataclass

from omnia_api.services import github_client
from omnia_api.services import repo as repo_svc

# Directories whose contents we always skip — they are noisy, huge, or
# generated and should never land in Omnia's file-store.
_SKIP_DIRS = (
    "/.git/",
    "/node_modules/",
    "/.next/",
    "/dist/",
    "/build/",
    "/__pycache__/",
    "/.venv/",
    "/vendor/",
)

# Regex for parsing GitHub URLs and owner/repo shorthands.
_GH_URL_RE = re.compile(
    r"^(?:https?://github\.com/)?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?$"
)


@dataclass
class ImportResult:
    files: dict[str, str]
    template: str
    truncated: bool
    skipped_binaries: int


def parse_github_url(url: str) -> tuple[str, str]:
    """Return (owner, repo) from a github.com URL or 'owner/repo' shorthand.

    Strips a trailing '.git' and trailing slash before matching so all of
    these are equivalent::

        https://github.com/owner/repo
        https://github.com/owner/repo.git
        owner/repo

    Raises ValueError for anything that doesn't match.
    """
    s = url.strip().removesuffix(".git").rstrip("/")
    m = _GH_URL_RE.match(s)
    if not m:
        raise ValueError(f"not a github repo url: {url!r}")
    return m.group(1), m.group(2)


async def fetch_repo_tarball(
    owner: str, repo: str, ref: str | None, token: str | None
) -> bytes:
    """Download the GitHub tarball for ``owner/repo`` at ``ref``.

    Uses the authenticated path when *token* is provided (required for private
    repos).  Raises FileNotFoundError on 404, PermissionError on 401/403.
    """
    ref_segment = ref or ""
    url = f"{github_client._GH_API}/repos/{owner}/{repo}/tarball/{ref_segment}".rstrip("/")
    if token:
        headers = github_client._auth_headers(token)
    else:
        headers = {"Accept": "application/vnd.github+json"}
    async with github_client._make_client(headers=headers) as client:
        resp = await client.get(url, follow_redirects=True)
    if resp.status_code == 404:
        raise FileNotFoundError("repo not found or private without token")
    if resp.status_code in (401, 403):
        raise PermissionError("forbidden — provide a GitHub token with repo scope")
    resp.raise_for_status()
    return resp.content


def tarball_to_files(tar_bytes: bytes) -> ImportResult:
    """Extract a GitHub tarball into a ``{rel_path: text_content}`` dict.

    GitHub wraps all entries under a single top-level directory
    (``owner-repo-<sha>/``). We strip that wrapper so callers get paths
    relative to the repo root.

    Rules applied (mirror repo._walk's contract):
    - Skip dirs listed in ``_SKIP_DIRS`` (git, node_modules, build artefacts…)
    - Skip files larger than ``repo_svc.MAX_FILE_BYTES``
    - Drop binary files (non-UTF-8); count them in ``skipped_binaries``
    - If more than ``MAX_FILES`` text files survive, keep the first 100 by
      sorted path and set ``truncated = True``
    """
    files: dict[str, str] = {}
    skipped_bin = 0

    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            # Strip GitHub's top-level wrapper dir: "owner-repo-sha/<rel>"
            parts = member.name.split("/", 1)
            if len(parts) < 2:
                continue
            rel = parts[1]
            if not rel:
                continue

            # Directory blocklist: match against "/" + rel lowercased
            low = "/" + rel.lower()
            if any(seg in low for seg in _SKIP_DIRS):
                continue

            # Size limit
            if member.size > repo_svc.MAX_FILE_BYTES:
                continue

            f = tf.extractfile(member)
            if f is None:
                continue
            raw = f.read()
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                skipped_bin += 1
                continue

            files[rel] = content

    truncated = len(files) > repo_svc.MAX_FILES
    if truncated:
        files = {k: files[k] for k in sorted(files)[: repo_svc.MAX_FILES]}

    return ImportResult(
        files=files,
        template=detect_template(files),
        truncated=truncated,
        skipped_binaries=skipped_bin,
    )


def detect_template(files: dict[str, str]) -> str:
    """Map a set of repo files to the closest Omnia template name.

    Heuristic (intentionally minimal — source='imported' is the real gate):
    - ``index.html`` at root + no ``package.json`` → ``"blank"``
      (pure static site; renders via the existing /p/<slug> path)
    - everything else → ``"code"``
      (language-agnostic file store; migration 0017; no container)
    """
    has_pkg = "package.json" in files
    if "index.html" in files and not has_pkg:
        return "blank"
    return "code"
