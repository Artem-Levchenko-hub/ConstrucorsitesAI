"""Conservative, read-only Distribution inventory and manifest retention policy.

The coordinator owns the maintenance fence, current workload roots and deletion
transport. It must put ``seen_path`` outside the live controller state root and
serialize maintenance. This module sends GET requests only; it never enables a
writable registry or deletes blobs. Unknown manifests retain their repository,
because an unsupported index may reference otherwise recognizable children.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import httpx

_GRACE_SECONDS = 7200
_PAGE_SIZE = 1000
_MAX_PAGES = 10000
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REPOSITORY = re.compile(r"max-app/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")
_NEXT_LINK = re.compile(r'\s*<([^<>]+)>\s*;\s*rel\s*=\s*(?:"next"|next)\s*\Z')
_DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"
_OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
_CONFIG_TYPES = {
    _DOCKER_MANIFEST: "application/vnd.docker.container.image.v1+json",
    _OCI_MANIFEST: "application/vnd.oci.image.config.v1+json",
}
_LAYER_TYPES = frozenset(
    {
        "application/vnd.docker.image.rootfs.diff.tar.gzip",
        "application/vnd.oci.image.layer.v1.tar",
        "application/vnd.oci.image.layer.v1.tar+gzip",
        "application/vnd.oci.image.layer.v1.tar+zstd",
    }
)
_ACCEPT = ", ".join(
    (
        _DOCKER_MANIFEST,
        _OCI_MANIFEST,
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.index.v1+json",
    )
)
type _FileIdentity = tuple[int, int, int, int, int]


class RegistryRetentionError(RuntimeError):
    """A complete, stable and safe registry inventory could not be established."""


@dataclass(frozen=True, slots=True)
class RegistryCandidate:
    repository: str
    digest: str
    config_digest: str
    tags: tuple[str, ...]

    @property
    def manifest_digest(self) -> str:
        return self.digest


@dataclass(frozen=True, slots=True)
class RegistryManifest:
    digest: str | None
    config_digest: str | None
    tags: tuple[str, ...]
    content_digest: str
    supported: bool


@dataclass(frozen=True, slots=True)
class RegistryRepository:
    repository: str
    tags: tuple[str, ...]
    manifests: tuple[RegistryManifest, ...]


@dataclass(frozen=True, slots=True)
class RegistryPlan:
    candidates: tuple[RegistryCandidate, ...]
    inventory: tuple[RegistryRepository, ...]
    observed_at: datetime
    registry: str
    observation_digest: str
    observation_identity: _FileIdentity


@dataclass(frozen=True, slots=True)
class _Observation:
    candidate: RegistryCandidate
    first_seen: datetime


@dataclass(frozen=True, slots=True)
class _SeenState:
    observations: dict[tuple[str, str], _Observation]
    observed_at: datetime | None
    digest: str | None
    identity: _FileIdentity | None


@dataclass(frozen=True, slots=True)
class _Protection:
    digests: frozenset[str]
    aliases: frozenset[tuple[str, str]]

    def protects(self, candidate: RegistryCandidate) -> bool:
        return (
            candidate.digest in self.digests
            or candidate.config_digest in self.digests
            or any((candidate.repository, tag) in self.aliases for tag in candidate.tags)
        )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode(content: bytes) -> object:
    value: object = json.loads(content, object_pairs_hook=_unique_object)
    return value


def _datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise RegistryRetentionError("registry observation timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("timezone required")
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        raise RegistryRetentionError("registry observation timestamp is invalid") from None


def _identity(value: os.stat_result) -> _FileIdentity:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _valid_descriptor(value: object, media_types: set[str] | frozenset[str]) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("mediaType"), str)
        and value.get("mediaType") in media_types
        and isinstance(value.get("digest"), str)
        and _DIGEST.fullmatch(value["digest"]) is not None
        and type(value.get("size")) is int
        and value["size"] >= 0
        and "urls" not in value
        and "data" not in value
    )


class RegistryRetention:
    """Observe immutable application manifests and apply an explicitly fenced plan."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        seen_path: Path,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        try:
            base = httpx.URL(base_url)
        except (httpx.InvalidURL, TypeError):
            raise RegistryRetentionError("registry base URL is invalid") from None
        if (
            base.scheme not in {"http", "https"}
            or not base.host
            or base.userinfo
            or base.path not in {"", "/"}
            or base.query
            or base.fragment
        ):
            raise RegistryRetentionError("registry base URL is invalid")
        self._base = base.copy_with(path="/")
        self._registry = str(self._base).rstrip("/")
        self._auth = httpx.BasicAuth(username, password)
        self._seen_path = seen_path.absolute()
        self._client = client if client is not None else httpx.Client()

    def __repr__(self) -> str:
        return "RegistryRetention()"

    def plan(self, protected: set[str], now: datetime) -> RegistryPlan:
        if now.utcoffset() is None:
            raise RegistryRetentionError("registry observation clock must include a timezone")
        now = now.astimezone(UTC)
        protection = self._protection(protected)
        previous = self._read_state()
        if previous.observed_at is not None and now < previous.observed_at:
            raise RegistryRetentionError("registry observation clock moved backwards")
        inventory = self._inventory()
        observations: dict[tuple[str, str], _Observation] = {}
        for repository in inventory:
            for manifest in repository.manifests:
                candidate = self._candidate(repository, manifest)
                if candidate is None:
                    continue
                key = (candidate.repository, candidate.digest)
                old = previous.observations.get(key)
                first_seen = old.first_seen if old and old.candidate == candidate else now
                observations[key] = _Observation(candidate, first_seen)
        receipt = self._write_state(observations, now, previous)
        assert receipt.digest is not None and receipt.identity is not None
        return RegistryPlan(
            candidates=self._eligible(inventory, observations, now, protection),
            inventory=inventory,
            observed_at=now,
            registry=self._registry,
            observation_digest=receipt.digest,
            observation_identity=receipt.identity,
        )

    def apply(
        self,
        plan: RegistryPlan,
        protected: set[str],
        *,
        delete: Callable[[str, str], int],
    ) -> dict[str, int]:
        """Recheck the entire plan before the first deletion, then each alias group.

        The injected transport must operate only during the coordinator's verified
        read-only maintenance lease and return Distribution's 202 status. Errors
        stop the sweep, including after any already acknowledged deletions.
        """
        roots = frozenset(protected)
        protection = self._protection(roots)
        state = self._assert_receipt(plan)
        eligible = self._eligible(
            plan.inventory,
            state.observations,
            plan.observed_at,
            _Protection(frozenset(), frozenset()),
        )
        if len(set(plan.candidates)) != len(plan.candidates) or any(
            candidate not in eligible for candidate in plan.candidates
        ):
            raise RegistryRetentionError("registry plan contains an unapproved candidate")
        if self._inventory() != plan.inventory:
            raise RegistryRetentionError("registry inventory changed after planning")
        self._assert_roots(protected, roots)
        self._assert_receipt(plan)
        expected = {repository.repository: repository for repository in plan.inventory}
        result = {"deleted": 0, "protected": 0}
        for candidate in plan.candidates:
            if protection.protects(candidate):
                result["protected"] += 1
                continue
            repository = expected[candidate.repository]
            manifest = next(
                item for item in repository.manifests if item.digest == candidate.digest
            )
            immutable = self._manifest(candidate.repository, candidate.digest)
            if replace(immutable, tags=manifest.tags) != manifest:
                raise RegistryRetentionError("registry manifest changed before deletion")
            if self._repository(candidate.repository) != repository:
                raise RegistryRetentionError("registry aliases changed before deletion")
            self._assert_receipt(plan)
            self._assert_roots(protected, roots)
            try:
                status = delete(candidate.repository, candidate.digest)
            except Exception:
                raise RegistryRetentionError("registry manifest deletion failed") from None
            if type(status) is not int or status != 202:
                raise RegistryRetentionError("registry manifest deletion was not acknowledged")
            result["deleted"] += 1
            expected[candidate.repository] = RegistryRepository(
                repository=candidate.repository,
                tags=tuple(tag for tag in repository.tags if tag not in candidate.tags),
                manifests=tuple(item for item in repository.manifests if item != manifest),
            )
        return result

    @staticmethod
    def _assert_roots(protected: set[str], expected: frozenset[str]) -> None:
        if protected != expected:
            raise RegistryRetentionError("protected registry roots changed during inventory")

    def _assert_receipt(self, plan: RegistryPlan) -> _SeenState:
        state = self._read_state()
        if (
            plan.registry != self._registry
            or state.digest != plan.observation_digest
            or state.identity != plan.observation_identity
            or state.observed_at != plan.observed_at
        ):
            raise RegistryRetentionError("registry observation receipt changed after planning")
        return state

    def _get(self, url: httpx.URL) -> httpx.Response:
        try:
            response = self._client.get(
                url,
                auth=self._auth,
                headers={"Accept": _ACCEPT},
                follow_redirects=False,
                timeout=30.0,
            )
        except Exception:
            raise RegistryRetentionError("registry inventory request failed") from None
        if response.status_code != 200:
            raise RegistryRetentionError("registry inventory request was not successful")
        return response

    def _list(self, path: str, field: str, *, repository: str | None = None) -> tuple[str, ...]:
        url = self._base.copy_with(path=path, query=f"n={_PAGE_SIZE}".encode())
        visited: set[str] = set()
        found: set[str] = set()
        for _ in range(_MAX_PAGES):
            if str(url) in visited:
                raise RegistryRetentionError("registry inventory pagination repeated a page")
            visited.add(str(url))
            response = self._get(url)
            try:
                payload = _decode(response.content)
            except (ValueError, UnicodeError):
                raise RegistryRetentionError("registry inventory response is invalid") from None
            if not isinstance(payload, dict) or field not in payload or "errors" in payload:
                raise RegistryRetentionError("registry inventory response is incomplete")
            if repository is not None and payload.get("name") != repository:
                raise RegistryRetentionError("registry tag inventory names a different repository")
            page = payload[field]
            if page is None and repository is not None:
                page = []
            if not isinstance(page, list) or any(
                not isinstance(item, str)
                or not item
                or (repository is not None and _TAG.fullmatch(item) is None)
                for item in page
            ):
                raise RegistryRetentionError("registry inventory entries are invalid")
            if len(set(page)) != len(page) or found.intersection(page):
                raise RegistryRetentionError("registry inventory pagination contains duplicates")
            found.update(page)
            links = response.headers.get_list("Link")
            if not links:
                return tuple(sorted(found))
            match = _NEXT_LINK.fullmatch(links[0]) if len(links) == 1 else None
            if match is None or not page:
                raise RegistryRetentionError("registry inventory pagination is incomplete")
            try:
                next_url = url.join(match[1])
            except (httpx.InvalidURL, ValueError):
                raise RegistryRetentionError("registry pagination URL is invalid") from None
            parameters = list(next_url.params.multi_items())
            if (
                (next_url.scheme, next_url.host, next_url.port)
                != (self._base.scheme, self._base.host, self._base.port)
                or next_url.userinfo
                or next_url.fragment
                or next_url.raw_path.split(b"?", 1)[0] != path.encode()
                or len(parameters) != 2
                or sorted(key for key, _ in parameters) != ["last", "n"]
                or next_url.params["last"] != page[-1]
                or not next_url.params["n"].isascii()
                or not next_url.params["n"].isdigit()
                or not 1 <= int(next_url.params["n"]) <= _PAGE_SIZE
            ):
                raise RegistryRetentionError("registry pagination escaped its inventory endpoint")
            url = next_url
        raise RegistryRetentionError("registry inventory has too many pages")

    def _inventory(self) -> tuple[RegistryRepository, ...]:
        names = self._list("/v2/_catalog", "repositories")
        repositories = tuple(name for name in names if _REPOSITORY.fullmatch(name))
        inventory = tuple(self._repository(repository) for repository in repositories)
        if self._list("/v2/_catalog", "repositories") != names:
            raise RegistryRetentionError("registry catalog changed during inventory")
        return inventory

    def _repository(self, repository: str) -> RegistryRepository:
        tags = self._list(f"/v2/{repository}/tags/list", "tags", repository=repository)
        manifests: dict[str, RegistryManifest] = {}
        for tag in tags:
            manifest = self._manifest(repository, tag)
            key = manifest.digest or manifest.content_digest
            previous = manifests.get(key)
            if previous is not None:
                if replace(previous, tags=()) != manifest:
                    raise RegistryRetentionError("registry manifest digest has ambiguous aliases")
                manifest = replace(manifest, tags=(*previous.tags, tag))
            else:
                manifest = replace(manifest, tags=(tag,))
            manifests[key] = manifest
        if self._list(f"/v2/{repository}/tags/list", "tags", repository=repository) != tags:
            raise RegistryRetentionError("registry tags changed during inventory")
        return RegistryRepository(
            repository, tags, tuple(manifests[key] for key in sorted(manifests))
        )

    def _manifest(self, repository: str, reference: str) -> RegistryManifest:
        response = self._get(self._base.copy_with(path=f"/v2/{repository}/manifests/{reference}"))
        content_digest = "sha256:" + sha256(response.content).hexdigest()
        header = response.headers.get("Docker-Content-Digest", "")
        digest = header if _DIGEST.fullmatch(header) else None
        media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
        try:
            payload = _decode(response.content)
        except (ValueError, UnicodeError):
            payload = None
        supported = (
            isinstance(payload, dict)
            and type(payload.get("schemaVersion")) is int
            and payload["schemaVersion"] == 2
            and media_type in _CONFIG_TYPES
            and payload.get("mediaType") == media_type
            and "manifests" not in payload
            and "artifactType" not in payload
            and "subject" not in payload
            and _valid_descriptor(payload.get("config"), {_CONFIG_TYPES[media_type]})
            and isinstance(payload.get("layers"), list)
            and all(_valid_descriptor(layer, _LAYER_TYPES) for layer in payload["layers"])
            and digest is not None
        )
        if supported and digest != content_digest:
            raise RegistryRetentionError("registry manifest content failed digest verification")
        if reference.startswith("sha256:") and (digest != reference or content_digest != reference):
            raise RegistryRetentionError("registry immutable manifest identity changed")
        config_digest: str | None = None
        if supported:
            assert isinstance(payload, dict)
            config = payload["config"]
            assert isinstance(config, dict) and isinstance(config["digest"], str)
            config_digest = config["digest"]
        return RegistryManifest(digest, config_digest, (), content_digest, supported)

    def _protection(self, protected: set[str] | frozenset[str]) -> _Protection:
        digests: set[str] = set()
        aliases: set[tuple[str, str]] = set()
        for original in protected:
            if not isinstance(original, str) or not original or original != original.strip():
                raise RegistryRetentionError("protected registry reference is invalid")
            reference = original
            for prefix in ("docker-pullable://", "docker://", "containerd://"):
                reference = reference.removeprefix(prefix)
            if _DIGEST.fullmatch(reference):
                digests.add(reference)
                continue
            if reference.startswith("sha256:"):
                raise RegistryRetentionError("protected registry digest is invalid")
            if "@" in reference:
                reference, digest = reference.rsplit("@", 1)
                if not _DIGEST.fullmatch(digest):
                    raise RegistryRetentionError("protected registry digest is invalid")
                digests.add(digest)
            if reference.startswith(("http://", "https://")):
                try:
                    url = httpx.URL(reference)
                except httpx.InvalidURL:
                    raise RegistryRetentionError("protected registry URL is invalid") from None
                if url.userinfo or url.query or url.fragment:
                    raise RegistryRetentionError("protected registry URL is invalid")
                if (url.host, url.port) != (self._base.host, self._base.port):
                    continue
                reference = url.path.removeprefix("/")
            elif not reference.startswith("max-app/"):
                host, separator, reference = reference.partition("/")
                if not separator:
                    continue
                try:
                    host_url = httpx.URL(f"{self._base.scheme}://{host}")
                except httpx.InvalidURL:
                    raise RegistryRetentionError("protected registry host is invalid") from None
                if host_url.userinfo or host_url.query or host_url.fragment:
                    raise RegistryRetentionError("protected registry host is invalid")
                if (host_url.host, host_url.port) != (self._base.host, self._base.port):
                    continue
            repository, separator, tag = reference.rpartition(":")
            if not separator:
                repository, tag = reference, "latest"
            if _REPOSITORY.fullmatch(repository):
                if not _TAG.fullmatch(tag):
                    raise RegistryRetentionError("protected registry tag is invalid")
                aliases.add((repository, tag))
        return _Protection(frozenset(digests), frozenset(aliases))

    @staticmethod
    def _candidate(
        repository: RegistryRepository, manifest: RegistryManifest
    ) -> RegistryCandidate | None:
        if not manifest.supported or manifest.digest is None or manifest.config_digest is None:
            return None
        return RegistryCandidate(
            repository.repository, manifest.digest, manifest.config_digest, manifest.tags
        )

    @classmethod
    def _eligible(
        cls,
        inventory: tuple[RegistryRepository, ...],
        observations: dict[tuple[str, str], _Observation],
        now: datetime,
        protection: _Protection,
    ) -> tuple[RegistryCandidate, ...]:
        candidates: list[RegistryCandidate] = []
        for repository in inventory:
            if any(not manifest.supported for manifest in repository.manifests):
                continue
            for manifest in repository.manifests:
                candidate = cls._candidate(repository, manifest)
                if candidate is None or protection.protects(candidate):
                    continue
                observed = observations.get((candidate.repository, candidate.digest))
                if (
                    observed is not None
                    and observed.candidate == candidate
                    and (now - observed.first_seen).total_seconds() >= _GRACE_SECONDS
                ):
                    candidates.append(candidate)
        return tuple(candidates)

    def _check_seen_path(self) -> None:
        if ".." in self._seen_path.parts:
            raise RegistryRetentionError("registry observation path is invalid")
        for path in (self._seen_path, *self._seen_path.parents):
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                raise RegistryRetentionError("registry observation path is unreadable") from None
            if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise RegistryRetentionError("registry observation path contains a link")
            if path != self._seen_path and not stat.S_ISDIR(info.st_mode):
                raise RegistryRetentionError("registry observation parent is not a directory")

    def _read_bytes(self) -> tuple[bytes | None, _FileIdentity | None]:
        self._check_seen_path()
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(self._seen_path, flags)
        except FileNotFoundError:
            return None, None
        except OSError:
            raise RegistryRetentionError("registry observation state is unreadable") from None
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise RegistryRetentionError("registry observation state is not a regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                content = stream.read()
            after = os.fstat(descriptor)
            current = self._seen_path.lstat()
            self._check_seen_path()
            # Python 3.12 Windows lstat reports creation time as st_ctime,
            # while fstat reports change time after a rename. Compare each
            # handle's full identity, and the common path identity separately.
            same_path = (
                _identity(after)[:4] == _identity(current)[:4]
                if os.name == "nt"
                else _identity(after) == _identity(current)
            )
            if _identity(before) != _identity(after) or not same_path:
                raise RegistryRetentionError("registry observation state changed while reading")
            return content, _identity(after)
        except OSError:
            raise RegistryRetentionError("registry observation state could not be read") from None
        finally:
            os.close(descriptor)

    def _read_state(self) -> _SeenState:
        content, identity = self._read_bytes()
        if content is None:
            return _SeenState({}, None, None, None)
        try:
            payload = _decode(content)
        except (ValueError, UnicodeError):
            raise RegistryRetentionError("registry observation state is invalid") from None
        if (
            not isinstance(payload, dict)
            or set(payload) != {"version", "registry", "observed_at", "observations"}
            or type(payload["version"]) is not int
            or payload["version"] != 1
            or payload["registry"] != self._registry
            or not isinstance(payload["observations"], list)
        ):
            raise RegistryRetentionError(
                "registry observation schema or registry identity is invalid"
            )
        observed_at = _datetime(payload["observed_at"])
        observations: dict[tuple[str, str], _Observation] = {}
        for entry in payload["observations"]:
            if (
                not isinstance(entry, dict)
                or set(entry) != {"repository", "digest", "config_digest", "tags", "first_seen"}
                or not isinstance(entry["repository"], str)
                or _REPOSITORY.fullmatch(entry["repository"]) is None
                or not isinstance(entry["digest"], str)
                or _DIGEST.fullmatch(entry["digest"]) is None
                or not isinstance(entry["config_digest"], str)
                or _DIGEST.fullmatch(entry["config_digest"]) is None
                or not isinstance(entry["tags"], list)
                or not entry["tags"]
                or any(not isinstance(tag, str) or not _TAG.fullmatch(tag) for tag in entry["tags"])
                or sorted(set(entry["tags"])) != entry["tags"]
            ):
                raise RegistryRetentionError("registry observation entries are invalid")
            candidate = RegistryCandidate(
                entry["repository"], entry["digest"], entry["config_digest"], tuple(entry["tags"])
            )
            first_seen = _datetime(entry["first_seen"])
            key = (candidate.repository, candidate.digest)
            if key in observations or first_seen > observed_at:
                raise RegistryRetentionError("registry observation history is inconsistent")
            observations[key] = _Observation(candidate, first_seen)
        return _SeenState(observations, observed_at, sha256(content).hexdigest(), identity)

    def _write_state(
        self,
        observations: dict[tuple[str, str], _Observation],
        now: datetime,
        previous: _SeenState,
    ) -> _SeenState:
        content = json.dumps(
            {
                "version": 1,
                "registry": self._registry,
                "observed_at": now.isoformat(),
                "observations": [
                    {
                        "repository": value.candidate.repository,
                        "digest": value.candidate.digest,
                        "config_digest": value.candidate.config_digest,
                        "tags": value.candidate.tags,
                        "first_seen": value.first_seen.isoformat(),
                    }
                    for _, value in sorted(observations.items())
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        temporary: str | None = None
        try:
            self._check_seen_path()
            self._seen_path.parent.mkdir(parents=True, exist_ok=True)
            self._check_seen_path()
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{self._seen_path.name}.", dir=self._seen_path.parent
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            current = self._read_state()
            if (current.digest, current.identity) != (previous.digest, previous.identity):
                raise RegistryRetentionError("registry observation state changed during inventory")
            os.replace(temporary, self._seen_path)
            temporary = None
            if os.name != "nt":
                directory = os.open(
                    self._seen_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                )
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            receipt = self._read_state()
            if receipt.digest != sha256(content).hexdigest():
                raise RegistryRetentionError(
                    "registry observation receipt changed during publication"
                )
            return receipt
        except OSError:
            raise RegistryRetentionError("registry observation state could not be saved") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
