"""Independent, create-only release bytes. Keys and errors carry no access URLs."""

import hashlib
import os
import re
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr


class BlobStore(Protocol):
    def put_verified(self, key: str, source: Path, sha256: str) -> str: ...

    def fetch_verified(self, key: str, destination: Path, sha256: str) -> Path: ...


def validate_key(key: str, sha256: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,240}", key) or not re.fullmatch(
        r"[0-9a-f]{64}", sha256
    ):
        raise ValueError("Invalid artifact identity")


def file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Regular artifact file required")
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _checked_write(chunks: Iterator[bytes], destination: Path, expected: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=destination.parent, prefix=".recovery-")
    temporary = Path(name)
    try:
        with os.fdopen(handle, "wb") as output:
            for chunk in chunks:
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if file_sha256(temporary) != expected:
            raise ValueError("Artifact integrity check failed")
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def _chunks(path: Path) -> Iterator[bytes]:
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            yield chunk


class DirectoryBlobStore:
    """Owner-controlled external directory; never used as live-storage evidence."""

    def __init__(self, root: Path):
        if root.is_symlink():
            raise ValueError("Storage root must not be a link")
        root.mkdir(parents=True, exist_ok=True)
        self.root = root

    def put_verified(self, key: str, source: Path, sha256: str) -> str:
        validate_key(key, sha256)
        if file_sha256(source) != sha256:
            raise ValueError("Artifact integrity check failed")
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            staged = _checked_write(_chunks(source), Path(temporary) / "blob", sha256)
            try:
                # Atomic create, unlike replace: older complete releases survive.
                os.link(staged, self.root / key)
            except FileExistsError:
                if file_sha256(self.root / key) != sha256:
                    raise ValueError("Immutable artifact already exists") from None
        return key

    def fetch_verified(self, key: str, destination: Path, sha256: str) -> Path:
        validate_key(key, sha256)
        source = self.root / key
        if file_sha256(source) != sha256:
            raise ValueError("Artifact integrity check failed")
        return _checked_write(_chunks(source), destination, sha256)


class GitHubReleaseAssetStore:
    """Exact numeric release, API asset IDs, no dependence on an old registry.

    GitHub permissions/deletion policy remain an external operational gate.
    Authenticity comes from a separately trusted manifest, never asset names.
    The caller owns the client lifetime. Credentials are sent only to GitHub.
    """

    def __init__(
        self, repository: str, release_id: int, *, client: httpx.Client,
        token: SecretStr | None = None,
    ):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or (
            type(release_id) is not int or release_id <= 0
        ):
            raise ValueError("Exact GitHub release identity required")
        self._repository = repository
        self._release_id = release_id
        self._client = client
        self._token = token

    def _headers(self, *, binary: bool = False) -> dict[str, str]:
        headers = {"Accept": "application/octet-stream" if binary else
                   "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self._token is not None:
            headers["Authorization"] = "Bearer " + self._token.get_secret_value()
        return headers

    def _asset_id(self, key: str) -> int | None:
        for page in range(1, 101):
            response = self._client.get(
                f"https://api.github.com/repos/{self._repository}/releases/"
                f"{self._release_id}/assets", params={"per_page": 100, "page": page},
                headers=self._headers(), follow_redirects=False,
            )
            if response.status_code != 200:
                raise ValueError("Independent release asset listing failed")
            items = response.json()
            if not isinstance(items, list):
                raise ValueError("Invalid release asset listing")
            matches = [item["id"] for item in items if item.get("name") == key]
            if matches:
                if len(matches) != 1 or type(matches[0]) is not int or matches[0] <= 0:
                    raise ValueError("Ambiguous release asset identity")
                return matches[0]
            if len(items) < 100:
                return None
        raise ValueError("Release asset listing limit exceeded")

    def fetch_verified(self, key: str, destination: Path, sha256: str) -> Path:
        validate_key(key, sha256)
        asset = self._asset_id(key)
        if asset is None:
            raise ValueError("Independent release artifact is missing")
        request = self._client.build_request(
            "GET", f"https://api.github.com/repos/{self._repository}/releases/assets/{asset}",
            headers=self._headers(binary=True),
        )
        response = self._client.send(request, stream=True, follow_redirects=False)
        try:
            if response.status_code == 302:
                location = response.headers.get("location", "")
                url = urlsplit(location)
                if url.scheme != "https" or url.hostname not in {
                    "release-assets.githubusercontent.com", "objects.githubusercontent.com"
                } or url.username or url.password or url.port not in {None, 443}:
                    raise ValueError("Untrusted artifact download redirect")
                response.close()
                request = self._client.build_request("GET", location)
                request.headers.pop("Authorization", None)
                request.headers.pop("Cookie", None)
                response = self._client.send(
                    request, stream=True, follow_redirects=False, auth=None
                )
            if response.status_code != 200:
                raise ValueError("Independent release artifact download failed")
            return _checked_write(response.iter_bytes(), destination, sha256)
        finally:
            response.close()

    def put_verified(self, key: str, source: Path, sha256: str) -> str:
        validate_key(key, sha256)
        if self._token is None or file_sha256(source) != sha256:
            raise ValueError("Verified artifact and release write credential required")
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory) / "blob"
            _checked_write(_chunks(source), temporary, sha256)
            if self._asset_id(key) is None:
                with temporary.open("rb") as content:
                    response = self._client.post(
                        f"https://uploads.github.com/repos/{self._repository}/releases/"
                        f"{self._release_id}/assets", params={"name": key},
                        headers=self._headers() | {"Content-Type": "application/octet-stream"},
                        content=content, follow_redirects=False,
                    )
                # 422 can mean a concurrent create. Verify its exact bytes below.
                if response.status_code not in {201, 422}:
                    raise ValueError("Independent release artifact upload failed")
            self.fetch_verified(key, Path(directory) / "readback", sha256)
        return key
