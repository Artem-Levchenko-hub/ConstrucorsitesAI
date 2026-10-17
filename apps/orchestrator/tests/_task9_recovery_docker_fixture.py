"""Guarded, nonce-owned Docker transport for the opt-in Task9 physical fixture.

Only topology is adapted: logical network records + network-none proxy/guard.
No recovery, checkpoint, identity, fence, receipt or database proof is replaced.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import docker

PYTHON_IMAGE = "sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58"
POSTGRES_IMAGE = "sha256:a3b7f434b2dc57ce85a67e171163eb8ab1a1ebcb39d27484661f26b1dfbe30d6"
LABEL = "task9.recovery_fixture"


def require(value, code):
    if not value:
        raise RuntimeError("fixture guard: " + code)


class ExecutionManifestMismatch(RuntimeError):
    """Safe inspect delta: field names and JSON types only, never inspected values."""

    def __init__(self, expected_config, actual_config, expected_host, actual_host):
        missing = object()
        types = {type(None): "null", bool: "bool", int: "int", float: "float",
                 str: "str", list: "list", dict: "dict"}
        differences = []
        for section, expected, actual in (
            ("Config", expected_config, actual_config),
            ("HostConfig", expected_host, actual_host),
        ):
            for key in sorted(set(expected) | set(actual)):
                before, after = expected.get(key, missing), actual.get(key, missing)
                if before == after:
                    continue
                field = key if re.fullmatch(r"[A-Z][A-Za-z0-9]{0,63}", key) else "<unknown>"
                before_type = "absent" if before is missing else types.get(type(before), "unknown")
                after_type = "absent" if after is missing else types.get(type(after), "unknown")
                differences.append(f"{section}.{field}:{before_type}->{after_type}")
        super().__init__("fixture guard: container execution manifest changed ["
                         + ",".join(differences) + "]")


def _canonical_host_config(host):
    # SDK 7.1 defaults False; Docker inspect may report that unset default as null.
    # The create allowlist forbids an explicit override. Keep every other field exact.
    result = dict(host)
    if "OomKillDisable" in result:
        value = result["OomKillDisable"]
        require(value is False or value is None, "nondefault OomKillDisable")
        result["OomKillDisable"] = None
    return result


def safe_fixture_error(exc):
    if isinstance(exc, ExecutionManifestMismatch):
        return str(exc)
    tests = Path(__file__).resolve().parent
    services = tests.parent / "src" / "yleum_orchestrator" / "services"
    # Fixed source labels + integer line coordinates only: no source text, locals or messages.
    allowed = {
        tests / "_task9_recovery_docker_fixture.py": "fixture_guard",
        tests / "test_adaptation_recovery_docker.py": "physical_fixture",
        **{services / (name + ".py"): name for name in (
            "docker_machine_backend", "docker_py_cell_backend", "machine_adapter",
            "project_machine", "machine_environment", "adaptation_recovery_engine",
        )},
    }
    frames = []
    trace = exc.__traceback__
    while trace is not None:
        label = allowed.get(Path(trace.tb_frame.f_code.co_filename).resolve())
        if label is not None:
            frames.append(f"{label}:{trace.tb_lineno}")
        trace = trace.tb_next
    suffix = " at " + ",".join(frames[-8:]) if frames else ""
    return type(exc).__name__ + suffix


class OwnedContainer:
    def __init__(self, scope, raw):
        self.scope, self.raw = scope, raw

    @property
    def attrs(self):
        attrs = self.raw.attrs
        if self.raw.labels.get("omnia.resource_kind") == "egress-proxy":
            # Topology-only address for the unchanged ensure() proxy lookup.
            attrs = {
                **attrs,
                "NetworkSettings": {"Networks": {self.scope.internal: {"IPAddress": "127.0.0.1"}}},
            }
        return attrs

    def __getattr__(self, key):
        value = getattr(self.raw, key)
        if key in {"start", "stop", "kill", "remove", "exec_run", "put_archive", "export"}:

            def guarded(*args, **kwargs):
                self.scope.check_container(self.raw)
                require(not kwargs.get("v"), "implicit volume removal")
                result = value(*args, **kwargs)
                if key == "export":

                    def measured():
                        digest = hashlib.sha256()
                        for chunk in result:
                            digest.update(chunk)
                            yield chunk
                        self.scope.exports[digest.hexdigest()] = self.raw.id

                    return measured()
                if key == "remove":
                    self.scope.removed.add(self.raw.id)
                return result

            return guarded
        require(key not in {"rename", "commit", "update"}, "unsupported container mutation")
        return value


class OwnedContainers:
    def __init__(self, scope):
        self.scope = scope

    def get(self, value):
        raw = self.scope.raw.containers.get(value)
        self.scope.check_container(raw)
        return OwnedContainer(self.scope, raw)

    def list(self, *args, **kwargs):
        # Read-only inventory may include foreign attachments; never adopt them.
        return [
            OwnedContainer(self.scope, raw)
            for raw in self.scope.raw.containers.list(*args, **kwargs)
        ]

    def create(self, image, command=None, **kwargs):
        scope = self.scope
        require(
            set(kwargs)
            <= {
                "name",
                "labels",
                "ports",
                "privileged",
                "cap_drop",
                "cap_add",
                "security_opt",
                "volumes",
                "network",
                "network_mode",
                "environment",
                "entrypoint",
                "detach",
                "user",
                "read_only",
                "mem_limit",
                "memswap_limit",
                "nano_cpus",
                "pids_limit",
                "tmpfs",
                "log_config",
                "working_dir",
                "auto_remove",
                "init",
                "ulimits",
            },
            "unsupported container option",
        )
        labels = kwargs.get("labels", {})
        scope.check_labels(labels)
        name = kwargs.get("name", "")
        require(
            name.startswith(scope.stem) or name.startswith(scope.workspace_volume[:48]),
            "foreign container name",
        )
        require(image in scope.allowed_images, "unpinned image")
        require(not kwargs.get("ports") and not kwargs.get("privileged"), "ports/privileged")
        require(not kwargs.get("devices") and not kwargs.get("volumes_from"), "host devices/mounts")
        require(
            "ALL" in kwargs.get("cap_drop", [])
            and "no-new-privileges:true" in kwargs.get("security_opt", []),
            "security profile",
        )
        mounts = kwargs.get("volumes", {})
        require(
            isinstance(mounts, dict) and set(mounts) <= scope.volumes_owned.keys(),
            "unowned volume mount",
        )
        for volume in mounts:
            scope.check_volume(scope.raw.volumes.get(volume))
        require(
            all(
                item.get("mode") in {"ro", "rw"} and str(item.get("bind", "")).startswith("/")
                for item in mounts.values()
            ),
            "invalid mount",
        )
        kind = labels.get("omnia.resource_kind")
        if kind in {"egress-proxy", "namespace-guard"}:
            # Only unchanged network transport: no iptables, proxy or egress.
            kwargs.pop("network", None)
            kwargs["network_mode"] = "none"
            kwargs["cap_add"] = []
            ready = ""
            if kind == "namespace-guard":
                ready = (
                    "print("
                    + repr("POLICY_READY=" + labels["omnia.policy_digest"])
                    + ",flush=True);"
                )
            command = [
                "python3",
                "-c",
                ready
                + "import signal,sys;signal.signal(signal.SIGTERM,lambda *_:sys.exit(0));"
                + "signal.pause()",
            ]
        mode = kwargs.get("network_mode", kwargs.get("network"))
        if kind in {"development", "project-postgres"}:
            require(mode in {"container:" + cid for cid in scope.guard_ids}, "foreign namespace")
            guard = scope.raw.containers.get(mode.removeprefix("container:"))
            scope.check_container(guard)
            require(
                guard.status == "running"
                and guard.labels.get("omnia.resource_kind") == "namespace-guard",
                "namespace is not current owned running guard",
            )
        else:
            require(mode == "none", "network access")
        # Environment is generated entirely by production code using synthetic fixture credentials.
        # No ambient env or env-file is forwarded by this transport or runner.
        require(
            kwargs.get("environment", {}) == scope.allowed_environments.get(kind, {}),
            "unexpected environment",
        )
        allowed_caps = {
            "development": {
                "CHOWN",
                "DAC_OVERRIDE",
                "FOWNER",
                "FSETID",
                "SETUID",
                "SETGID",
                "SETFCAP",
                "KILL",
                "NET_BIND_SERVICE",
                "SYS_CHROOT",
            },
            "project-postgres-prepare": {"CHOWN", "DAC_OVERRIDE"},
        }
        require(
            set(kwargs.get("cap_add", [])) <= allowed_caps.get(kind, set()), "capability escalation"
        )
        kwargs["labels"] = {**labels, LABEL: scope.nonce}
        # Docker SDK create() raises ImageNotFound; it never pulls (7.1 cached source).
        try:
            scope.raw.containers.get(name)
        except docker.errors.NotFound:
            pass
        else:
            raise RuntimeError("fixture guard: preexisting container name")
        raw = scope.raw.containers.create(image, command, **kwargs)
        raw.reload()
        scope.containers_owned[raw.id] = {
            "name": raw.name,
            "image": raw.attrs["Image"],
            "labels": dict(raw.labels),
            "network": mode,
            "mounts": {k: dict(v) for k, v in mounts.items()},
            "config": deepcopy(raw.attrs.get("Config", {})),
            "host_config": deepcopy(raw.attrs.get("HostConfig", {})),
        }
        scope.check_container(raw)
        if kind == "namespace-guard":
            scope.guard_ids.add(raw.id)
        return OwnedContainer(scope, raw)


class OwnedVolume:
    def __init__(self, scope, raw):
        self.scope, self.raw = scope, raw

    def __getattr__(self, key):
        require(key != "remove", "use guarded volume removal")
        return getattr(self.raw, key)

    def remove(self, force=False):
        require(not force, "forced volume removal")
        self.scope.check_volume(self.raw)
        require(
            not self.scope.raw.containers.list(all=True, filters={"volume": self.raw.name}),
            "volume remains attached",
        )
        self.raw.remove(force=False)
        self.scope.volumes_removed.add(self.raw.name)


class OwnedVolumes:
    def __init__(self, scope):
        self.scope = scope

    def get(self, name):
        raw = self.scope.raw.volumes.get(name)
        self.scope.check_volume(raw)
        return OwnedVolume(self.scope, raw)

    def list(self, *args, **kwargs):
        return [
            OwnedVolume(self.scope, raw) for raw in self.scope.raw.volumes.list(*args, **kwargs)
        ]

    def create(self, name, labels, **kwargs):
        scope = self.scope
        scope.check_labels(labels)
        require(name in scope.allowed_volumes, "unexpected volume name")
        require(
            not kwargs.get("driver_opts") and kwargs.get("driver", "local") == "local",
            "nonlocal volume",
        )
        try:
            scope.raw.volumes.get(name)
        except docker.errors.NotFound:
            pass
        else:
            raise RuntimeError("fixture guard: preexisting volume name")
        raw = scope.raw.volumes.create(name=name, labels={**labels, LABEL: scope.nonce}, **kwargs)
        scope.volumes_owned[name] = {
            key: raw.attrs.get(key)
            for key in ("Name", "CreatedAt", "Driver", "Mountpoint", "Labels")
        }
        scope.check_volume(raw)
        return OwnedVolume(scope, raw)


class LogicalNetworks:
    """No Docker network mutation: explicitly scoped replacement for transport only."""

    def __init__(self, scope):
        self.scope = scope

    def get(self, name):
        scope = self.scope
        require(name in {scope.internal, scope.stem + "-public"}, "foreign network name")
        labels = {**scope.identity, "omnia.resource_kind": "public-egress"}
        return SimpleNamespace(
            name=name,
            attrs={"Internal": name == scope.internal, "Labels": labels},
            connect=lambda _: None,
            remove=lambda: None,
        )

    def list(self, **kwargs):
        return []

    def create(self, *args, **kwargs):
        raise RuntimeError("fixture guard: real network creation forbidden")


class OwnedAPI:
    def __init__(self, scope):
        self.scope = scope

    def import_image(self, *, src, changes):
        scope = self.scope
        expected = [
            f"LABEL {k}={v}"
            for k, v in {**scope.identity, "omnia.resource_kind": "environment"}.items()
        ]
        require(set(changes) == set(expected), "captured image labels/provenance")
        # Actual production rootfs export/import. No configuration or base-image mutation.
        position = src.tell()
        digest = hashlib.sha256()
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(chunk)
        src.seek(position)
        require(digest.hexdigest() in scope.exports, "image lacks owned export provenance")
        result = scope.raw.api.import_image(
            src=src, changes=[*changes, f"LABEL {LABEL}={scope.nonce}"]
        )
        decoded = result.decode() if isinstance(result, bytes) else result
        records = [json.loads(line) for line in decoded.splitlines() if line.strip()]
        created = [
            r.get("status") for r in records if str(r.get("status", "")).startswith("sha256:")
        ]
        require(
            len(created) == 1 and not any("error" in r for r in records), "image import outcome"
        )
        image = scope.raw.images.get(created[0])
        config = image.attrs.get("Config") or {}
        require(
            image.labels.get(LABEL) == scope.nonce
            and not config.get("Env")
            and not config.get("Cmd")
            and not config.get("Entrypoint"),
            "captured image contains runtime config",
        )
        scope.allowed_images.add(image.id)
        scope.images_owned.add(image.id)
        return result

    def __getattr__(self, key):
        require(
            key in {"exec_create", "exec_start", "exec_inspect", "inspect_container"},
            "unsupported raw Docker API",
        )
        value = getattr(self.scope.raw.api, key)

        def guarded(*args, **kwargs):
            if key in {"exec_create", "inspect_container"}:
                target = args[0] if args else kwargs.get("container")
                container = self.scope.raw.containers.get(target)
                self.scope.check_container(container)
            else:
                exec_id = args[0] if args else kwargs.get("exec_id")
                target = self.scope.execs_owned.get(exec_id)
                require(target is not None, "unowned exec")
                self.scope.check_container(self.scope.raw.containers.get(target))
                require(
                    self.scope.raw.api.exec_inspect(exec_id).get("ContainerID") == target,
                    "exec container changed",
                )
            result = value(*args, **kwargs)
            if key == "exec_create":
                exec_id = result.get("Id")
                require(isinstance(exec_id, str) and len(exec_id) == 64, "invalid exec identity")
                self.scope.execs_owned[exec_id] = container.id
            return result

        return guarded


class FixtureDocker:
    def __init__(self, raw, *, nonce, backend):
        self.raw, self.nonce = raw, nonce
        self.stem, self.internal = backend.stem, backend.internal_network
        self.workspace_volume = backend.workspace_volume
        self.identity = backend.labels("environment")
        self.identity.pop("omnia.resource_kind")
        self.allowed_images = {PYTHON_IMAGE, POSTGRES_IMAGE}
        for image in self.allowed_images:
            require(raw.images.get(image).id == image, "cached image mismatch")
        self.allowed_volumes = set()
        self.allowed_environments = {}
        self.volumes_owned, self.containers_owned = {}, {}
        self.exports = {}
        self.execs_owned = {}
        self.volumes_removed = set()
        self.guard_ids, self.images_owned, self.removed = set(), set(), set()
        self.containers, self.volumes = OwnedContainers(self), OwnedVolumes(self)
        self.networks, self.api = LogicalNetworks(self), OwnedAPI(self)
        self.images = SimpleNamespace(get=self.image)
        self.info = raw.info

    def image(self, value):
        require(value in self.allowed_images, "foreign image read")
        return self.raw.images.get(value)

    def check_labels(self, labels):
        require(
            all(
                labels.get(k) == v for k, v in self.identity.items() if k != "omnia.project_machine"
            ),
            "foreign resource identity",
        )

    def check_volume(self, raw):
        raw.reload()
        known = self.volumes_owned.get(raw.name)
        require(
            known is not None and raw.attrs.get("Labels", {}).get(LABEL) == self.nonce,
            "unowned volume",
        )
        require(all(raw.attrs.get(k) == v for k, v in known.items()), "changed volume instance")
        require(
            raw.attrs.get("Driver") == "local" and not raw.attrs.get("Options"), "volume driver"
        )

    def _namespace_configs(self, raw, config, known):
        expected = known["config"]
        mode = known["network"]
        if not mode.startswith("container:"):
            return config, expected
        parent_id = mode.removeprefix("container:")
        require(
            parent_id in self.guard_ids and parent_id != raw.id
            and known["labels"].get("omnia.resource_kind") in {"development", "project-postgres"},
            "foreign namespace hostname",
        )
        parent = self.raw.containers.get(parent_id)
        parent_known = self.containers_owned.get(parent_id)
        require(
            parent.id == parent_id and parent_known is not None
            and parent_known["network"] == "none"
            and parent_known["labels"].get("omnia.resource_kind") == "namespace-guard",
            "namespace parent identity changed",
        )
        self.check_container(parent)  # Fresh full pinned identity/config/mount check.
        hostname = parent.attrs["Config"].get("Hostname")
        require(hostname == parent_id[:12], "namespace parent hostname changed")
        require(expected.get("Hostname") in {raw.id[:12], hostname}, "unknown original hostname")
        allowed = {raw.id[:12], hostname} if raw.status == "created" else {hostname}
        require(config.get("Hostname") in allowed, "unknown namespace hostname")
        # Keep original evidence intact; only this verified namespace-derived field differs.
        return {**config, "Hostname": hostname}, {**expected, "Hostname": hostname}

    def check_container(self, raw):
        raw.reload()
        known = self.containers_owned.get(raw.id)
        require(known is not None, "unowned container")
        cfg, host = raw.attrs.get("Config", {}), raw.attrs.get("HostConfig", {})
        require(
            raw.name == known["name"]
            and raw.attrs.get("Image") == known["image"]
            and cfg.get("Labels") == known["labels"]
            and cfg["Labels"].get(LABEL) == self.nonce,
            "container identity changed",
        )
        require(
            host.get("NetworkMode") == known["network"]
            and not host.get("PortBindings")
            and not host.get("Privileged"),
            "container isolation changed",
        )
        actual_config, expected_config = self._namespace_configs(raw, cfg, known)
        if (actual_config != expected_config
                or _canonical_host_config(host) != _canonical_host_config(known["host_config"])):
            raise ExecutionManifestMismatch(known["config"], cfg, known["host_config"], host)
        actual = {
            m["Name"]: {"bind": m["Destination"], "mode": "rw" if m["RW"] else "ro"}
            for m in raw.attrs.get("Mounts", [])
            if m.get("Type") == "volume"
        }
        require(
            actual == known["mounts"]
            and all(m.get("Type") in {"volume", "tmpfs"} for m in raw.attrs.get("Mounts", [])),
            "container mounts changed",
        )

    def cleanup(self):
        for cid in reversed(list(self.containers_owned)):
            try:
                raw = self.raw.containers.get(cid)
            except docker.errors.NotFound:
                # Includes Docker auto_remove helpers; existence was pinned at creation.
                self.removed.add(cid)
                continue
            self.check_container(raw)
            raw.stop(timeout=5)
            try:
                self.check_container(raw)
                raw.remove()  # no force, no implicit volume deletion
            except docker.errors.NotFound:
                require(
                    self.containers_owned[cid]["host_config"].get("AutoRemove"),
                    "container vanished outside guarded cleanup",
                )
            self.removed.add(cid)
        for name in reversed(list(self.volumes_owned)):
            try:
                raw = self.raw.volumes.get(name)
            except docker.errors.NotFound:
                require(name in self.volumes_removed, "volume vanished outside guarded cleanup")
                continue
            self.check_volume(raw)
            require(
                not self.raw.containers.list(all=True, filters={"volume": name}),
                "volume remains attached",
            )
            raw.remove(force=False)
            self.volumes_removed.add(name)
        for image_id in self.images_owned:
            image = self.raw.images.get(image_id)
            require(
                image.id == image_id and image.labels.get(LABEL) == self.nonce,
                "foreign image cleanup",
            )
            require(
                not self.raw.containers.list(all=True, filters={"ancestor": image_id}),
                "image remains used",
            )
            self.raw.images.remove(image_id, force=False, noprune=True)
        return {
            "containers_created": len(self.containers_owned),
            "containers_removed": len(self.removed),
            "volumes_removed": len(self.volumes_removed),
            "images_removed": len(self.images_owned),
        }
