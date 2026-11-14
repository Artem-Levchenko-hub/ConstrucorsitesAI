#!/usr/bin/env python3
"""Read-only publication quiescence gate using the live controller's settings."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5


class PhysicalDrainError(RuntimeError):
    """Only fixed structural reasons, never Docker exception bodies."""


class ProcessDisappeared(PhysicalDrainError):
    """An incomplete sample, only after the exact proc directory is absent."""


_MACHINE_HELPERS = frozenset(
    {
        "project-postgres-prepare",
        "project-postgres-init",
        "project-postgres-restore",
        "retained-proof",
        "restore-check",
        "archive",
        "core-database-bootstrap",
    }
)
_MACHINE_SERVICES = frozenset(
    {
        "development",
        "project-postgres",
        "namespace-guard",
        "egress-proxy",
        "managed-max-core",
        "max-gateway",
    }
)


_PROC_ROOT = Path("/proc")
_CGROUP_ROOT = Path("/sys/fs/cgroup")
_QUIESCENCE_ROOT = Path("/var/lib/yleum/machine-quiescence")
# Reviewed portable-child producer/lock contract, as shipped in 8e8f7d94.
# New source must be reviewed and explicitly added; a hash in a receipt alone
# cannot establish that an unknown implementation never creates containers.
_PORTABLE_QUIESCENCE_SOURCES = {
    "services.project_machine": "c265481789481f8b4f3f486f4f44345cdf835759204f44b1c7d5667373f171b3",
    "services.docker_machine_backend": "8c963461e50b3bc3aa29d564a711ee5a656356d7f6fd65c739d9ae74288aa10a",
    "services.machine_adapter": "7a8fee13333fee8a79478bc9f64b895a67b1deed284335355158b8a95103d426",
    "routers.workspace": "4ad3af972da6e73af3e15eb407cff041f15321cf9af83466502c14c2f6dd0dc6",
    "services.cell_state": "c506c7071f9b067f7819745c104d207dc496209c576bce3179f505516f2a1d42",
    "services.cell_lock": "1bf272c831eec8446270adf098de101db2aac6e02b5f18ea86ff43c81bfb39b3",
    "services.workspace_provider_factory": "5fc8c2f122c00f9da29439467849b94bcf4eaf3dad11a7d7f04111cc592b7a9d",
    "services.generation_reader_inventory": "119a83b06d26c2936d1fdf6140c824cb53fee761d7f0d4fe5b8465f34ed04a6e",
    "services.docker_cell_resources": "6a7f931d4a17bc4517137de069186eb6bb4434e65ce32429de3ac46b3ff52710",
}
# The initial reviewed producer contract remains valid on live b781. The
# reviewed successor (6e5fa4f7) adds a pre-effect missing-draft rejection and its
# typed route response. The 641c6eed successor adds only a fenced read-only
# source snapshot route, with no new producer or lock semantics.
# These two modules must come from the SAME bundle; an
# independently allowed hash per module would also trust unreviewed mixed code.
_PORTABLE_QUIESCENCE_PAIRED_SOURCES = ("services.machine_adapter", "routers.workspace")
_PORTABLE_QUIESCENCE_SOURCE_PAIRS = (
    (
        "7a8fee13333fee8a79478bc9f64b895a67b1deed284335355158b8a95103d426",
        "4ad3af972da6e73af3e15eb407cff041f15321cf9af83466502c14c2f6dd0dc6",
    ),
    (
        "944528d2805e971120b32336d42adc6544538043fc60ff4c6219de10aabc2611",
        "f82e0bb183f1258cccf393d58e9ee047a6b15cf74b2c3ffdb98fd9111c525e29",
    ),
    (
        "944528d2805e971120b32336d42adc6544538043fc60ff4c6219de10aabc2611",
        "25a841e7d4b4f9535c0fe2f415bac0172874ad2bf1fce83b82b7c28792db5213",
    ),
)
# d30c7bae9e21e7b783bc439ee7a2c313de6ebd04: the catalog file projection and
# generation seed retain the existing operation lock, fencing, reader_dispatch,
# and cancellation-drained machine_effect boundaries. Status only reads Docker
# archives/HTTP; app_business_config writes public data atomically, never starts
# containers, execs, or processes. Pin that new transitive dependency as part of
# this complete bundle, not as an independently interchangeable helper hash.
_PORTABLE_QUIESCENCE_CATALOG_SOURCES = {
    **_PORTABLE_QUIESCENCE_SOURCES,
    "services.docker_machine_backend": "f97cc42908ac85602ffbd006d3aaad6845b4dbd1a74cf23c13b55fdb282a6a4e",
    "services.machine_adapter": "80b8a8033be6c314ffcd0bba93c027cce6b50b68a7197ed93078d115254cb661",
    "routers.workspace": "a32fedaf6bb0bca42950a8f1f777937314154ef6d93436b4a0d7610423dc769a",
    "services.app_business_config": "2e6a05f2b43773fbb8ae5b0fcdf9effdb1ace86782592fa74d2dba363cd85601",
}
# e5adb011fd4e9a78629ef8c7caff60d582bc9b06 runtime observed by the expanded
# collector: the catalog producer is unchanged, but these pre-existing helpers
# are present too. Egress/origin helpers do not create Docker work; the adapter
# uses the published backend only for its schema inspector under machine_effect.
# Preserve the historical catalog10 and admit only this complete legacy13 set.
_PORTABLE_QUIESCENCE_LEGACY_CATALOG_SOURCES = {
    **_PORTABLE_QUIESCENCE_CATALOG_SOURCES,
    "services.machine_egress": "52004eefcde30d95978d02d8cf977820cce60c789adf6e6f634fc62f28d531af",
    "services.published_machine_backend": "ed0cfb033757f04f48a4d87e154ad2f05d49e5cd40809f64df9677565ce633be",
    "services.studio_origins": "2f6a5f03ede22d8e72f453f5ebde2c7a0e28f7b31a5037565d88beab38cb3a45",
}
# Owner-management successor: public config remains atomic; published recovery
# quiesces/recreates only accepted compute and retains volumes. The stateless
# receipt proxy runs controller bytes, keeps its enforced internal address on
# upgrade, and uses the existing reader_dispatch boundary. Pin its new helper
# and identity/origin dependencies together with the production lifecycle.
_PORTABLE_QUIESCENCE_OWNER_SOURCES = {
    **_PORTABLE_QUIESCENCE_CATALOG_SOURCES,
    "services.docker_machine_backend": "74caba16187c229a257e66ccedf18c4ee17c07ed2612411b8eef68f19b697138",
    "services.published_machine_backend": "3316b751db9d424cd139758b3c0784c052fb32f1db67fe52c5710b25948e1805",
    "services.owner_management_identity": "589baa7e32e61432e36cfab21efd69602170cb79a1bf24471af4a375a50a8104",
    "services.machine_egress": "78dceaba28d412f9dbb2c590ec955d51a9b7c47df0d4a342fdcc0e6111ad8db1",
    "services.studio_origins": "2f6a5f03ede22d8e72f453f5ebde2c7a0e28f7b31a5037565d88beab38cb3a45",
}
# CONNECT startup successor: only opaque proxy stream pacing changes. It adds no
# Docker/process producer or dependency, preserves pins/denials and cancellation
# boundaries, and bounds its per-connection pacing and per-write deadline. Keep
# historical owner14 authority intact; admit this complete successor, not mixed
# per-module alternatives.
_PORTABLE_QUIESCENCE_CONNECT_SOURCES = {
    **_PORTABLE_QUIESCENCE_OWNER_SOURCES,
    "services.machine_egress": "60ed890167ed85c5e326dfdc09cf10a139be0cd74bf90ed027470b2f38adf627",
}
# Independently reviewed Native startup successor (frozen ordinary V2). GET never
# starts compute; the explicit fenced prepare persists intent before container
# creation. Pending startup-prepares are NOT portable child-command retirement:
# both global drain and scoped root attestation reject them. Bind the new typed
# journal and its existing schema/receipt dependencies as one complete bundle.
_PORTABLE_QUIESCENCE_NATIVE_STARTUP_SOURCES = {
    **_PORTABLE_QUIESCENCE_CONNECT_SOURCES,
    "core.cell_resources": "f05851b80fb9d13a0f69c75d7dbb098ec1082562c13eccff0e93df7ad659c3c4",
    "routers.workspace": "7d53afffcceab86d89f39ddbc6063de9d70c41cc78e27063b75bffa176e970ac",
    "schemas.workspace": "d7d281648481df4fcde770828948e3580224dba33729015bbf40c6c28c49b1ff",
    "services.cell_state": "08fb69ff2eae48aaa778da24f495de21f6e0403a0ba7e4f1c2518bc90efba9a5",
    "services.command_receipts": "44dd8cd1706e55eb46b10c7c05b4ff19dac0b642326cb5697e235c8a1f9e03e4",
    "services.docker_cell_resources": "cadc34e78fb96da273098be7320541a55e1183bbc8483c5f7312423e7f87312d",
    "services.docker_machine_backend": "d3495ddfa05e15564394bd5b8dacc69e41f2e8ee202e3f6926a6d25ff839121c",
    "services.machine_adapter": "22d05923dd63b9e5aeae4994d3d0d988f6876e868a667eaa61a1448665d759d6",
    "services.machine_startup": "0220a5d7f2be0da6c97012a9ca53272bd19331f40d282d576462a9445d64e71a",
    "services.project_machine": "8ff389bd8194c3f7c043e62c7403ded15cea5c0deaedd11c9c90e9142960faa6",
    "services.source_write_journal": "dcb5593846c3c75b277f62cd8310406f19564208cd5a0678bea13840671d6c21",
}
# FULL_BUILD startup successor: the existing fenced command captures and binds
# a same-content process transition to its compilation and migration receipts.
# The helper only constructs metadata; routing keeps the same reader_dispatch,
# machine_effect and command lock boundaries. Admit the complete reviewed pair
# over Native19, never either new module mixed with its historical counterpart.
_PORTABLE_QUIESCENCE_FULL_BUILD_STARTUP_SOURCES = {
    **_PORTABLE_QUIESCENCE_NATIVE_STARTUP_SOURCES,
    "routers.workspace": "8eb9c5504c1bc85e694261a1289bfa1a03259e4473a903819240bb9ca4d95008",
    "services.machine_startup": "9b02eae20e5760f6735f9fb9dbf1f2c1150bc8291662baf71d31af630f85b794",
}
# Reviewed platform-bootstrap v2 successor: the existing fenced BOOTSTRAP role
# may apply only the controller-owned management migration. The route retains
# its operation/command locks and machine_effect/reader_dispatch boundaries;
# project_migrations supplies the exact SQL selection/execution contract. Bind
# that helper only after matching this complete producer, never as a substitute
# for historical FULL_BUILD19 or as independently interchangeable source hashes.
_PORTABLE_QUIESCENCE_PLATFORM_BOOTSTRAP_V2_SOURCES = {
    **_PORTABLE_QUIESCENCE_FULL_BUILD_STARTUP_SOURCES,
    "routers.workspace": "607857f5c390cfc45450bb6d4cb1f82ab44fa3828b45e5e75f16379d8579f2a5",
    "services.machine_adapter": "bac93a863ee7eb73ad023f21cc53c08f2ecc039c38a2895ff6778a053ba5cc30",
    "services.project_migrations": (
        "3f68b1cd69a260aad7afee8d5069cf43218a896ec993d8ae157e7ec36b0db0eb"
    ),
}
# Next route declarations are refreshed inside the existing fenced command
# child, after install and before unchanged compiler/tests. This helper adds no
# container/startup/lock authority. Bind it together with its adapter, preserving
# the complete historical platform-bootstrap bundle without substitutions.
_PORTABLE_QUIESCENCE_NEXT_TYPE_PREPARATION_SOURCES = {
    **_PORTABLE_QUIESCENCE_PLATFORM_BOOTSTRAP_V2_SOURCES,
    "services.machine_adapter": "e1624a7d0944e087b4656f2c96881cf15cdd43346cf35b5164d282b67f3700d1",
    "services.next_type_preparation": (
        "df56b577a9739c17b61ec9939d63dfc1c21a5bdaefe26a891089ce786011ac0d"
    ),
}
# A repeated BOOTSTRAP verifies journaled platform SQL without stopping the
# product. Bind the adapter change to the same complete controller closure.
_PORTABLE_QUIESCENCE_BOOTSTRAP_RECHECK_SOURCES = {
    **_PORTABLE_QUIESCENCE_NEXT_TYPE_PREPARATION_SOURCES,
    "services.machine_adapter": "22f53409ddc90d47572ad5bb4e7e26d1d7c37a79023fc30e9cd371ca87b6686a",
}
# A reader gap retries the migration post-check under the existing workspace
# lock. A protected-source startup rejection remains pre-effect. Neither
# change admits a new physical writer or loosens the historical source proof.
_PORTABLE_QUIESCENCE_SOURCE_GUARD_RETRY_SOURCES = {
    **_PORTABLE_QUIESCENCE_BOOTSTRAP_RECHECK_SOURCES,
    "services.machine_adapter": "4c1d390e6519f8a95291308c6f0c5eabeaa552a5446067bd80b9e491c3ff806c",
    "services.docker_cell_resources": "bc59357b8c73c356303399e5a35f8f1c0c09a96f5917f0cd93a2bde4d2d173cc",
}
# The startup checker recognizes a receipt for one terminal fast_check only.
# Operator proof uses the existing workspace lock and never writes guest state.
_PORTABLE_QUIESCENCE_SOURCE_GUARD_RECOVERY_SOURCES = {
    **_PORTABLE_QUIESCENCE_SOURCE_GUARD_RETRY_SOURCES,
    "services.machine_startup": "fd2c1034948bd967907aee922862ac4f91ec2b3bd08ac040f61f21449489206b",
}
# Lease observation uses the same receipt check before sampling physical writers.
_PORTABLE_QUIESCENCE_SOURCE_GUARD_LEASE_SOURCES = {
    **_PORTABLE_QUIESCENCE_SOURCE_GUARD_RECOVERY_SOURCES,
    "services.machine_adapter": "441c57fc35eaf1d4e7037c6b62687e8d680fe55969b518abaf84a89b496bd94d",
}
# Build-role dispatch now waits within the existing lock and rechecks authority
# before calling Docker. The controller journals and child UUIDs are unchanged.
_PORTABLE_QUIESCENCE_READER_WAIT_SOURCES = {
    **_PORTABLE_QUIESCENCE_SOURCE_GUARD_LEASE_SOURCES,
    "services.generation_reader_inventory": "094423193b883820e3360d6db54ca9bc3c03c2c48347c9ab0f8eeaedfd0f3c0c",
    "services.machine_adapter": "98fd7d44428611fdcdd36f1fbf51735e494d16fee759322e7d772e780bdd2b89",
    "routers.workspace": "25e167c1832959ecafd47f970735a113d56e1ae7630b557f773209d9a270c742",
}
# The Meal probe mints a draft session under the existing workspace lock and
# generation fence. It reuses the ready-preview path and its process inventory.
_PORTABLE_QUIESCENCE_MEAL_ACCEPTANCE_SOURCES = {
    **_PORTABLE_QUIESCENCE_READER_WAIT_SOURCES,
    "routers.workspace": "5ca6c97f48cda7011e242c86970b5ebec56abf7ba2e935dcb9e180990aa2be1f",
    "schemas.workspace": "6bf206e34d5d35717d2e518be87944915df876e666fdbc7219ad5e037e5c6db1",
    "services.generation_reader_inventory": (
        "230b53167ab847a4fdb9ce23460a1d3da6d5cde7c0c754a81a70221da9bb0161"
    ),
}
_PORTABLE_QUIESCENCE_SOURCE_BUNDLES = tuple(
    {
        **_PORTABLE_QUIESCENCE_SOURCES,
        **dict(zip(_PORTABLE_QUIESCENCE_PAIRED_SOURCES, pair, strict=True)),
    }
    for pair in _PORTABLE_QUIESCENCE_SOURCE_PAIRS
) + (
    _PORTABLE_QUIESCENCE_CATALOG_SOURCES,
    _PORTABLE_QUIESCENCE_LEGACY_CATALOG_SOURCES,
    _PORTABLE_QUIESCENCE_OWNER_SOURCES,
    _PORTABLE_QUIESCENCE_CONNECT_SOURCES,
    _PORTABLE_QUIESCENCE_NATIVE_STARTUP_SOURCES,
    _PORTABLE_QUIESCENCE_FULL_BUILD_STARTUP_SOURCES,
    _PORTABLE_QUIESCENCE_PLATFORM_BOOTSTRAP_V2_SOURCES,
    _PORTABLE_QUIESCENCE_NEXT_TYPE_PREPARATION_SOURCES,
    _PORTABLE_QUIESCENCE_BOOTSTRAP_RECHECK_SOURCES,
    _PORTABLE_QUIESCENCE_SOURCE_GUARD_RETRY_SOURCES,
    _PORTABLE_QUIESCENCE_SOURCE_GUARD_RECOVERY_SOURCES,
    _PORTABLE_QUIESCENCE_SOURCE_GUARD_LEASE_SOURCES,
    _PORTABLE_QUIESCENCE_READER_WAIT_SOURCES,
    _PORTABLE_QUIESCENCE_MEAL_ACCEPTANCE_SOURCES,
)
# These modules already exist in old releases, but their new startup/bootstrap contracts
# are authority only for a complete successor producer bundle.
_PORTABLE_QUIESCENCE_CONDITIONAL_SOURCE_NAMES = frozenset(
    {
        "schemas.workspace",
        "core.cell_resources",
        "services.source_write_journal",
        "services.command_receipts",
        "services.project_migrations",
    }
)
_PORTABLE_QUIESCENCE_SOURCE_NAMES = tuple(
    sorted(
        (
            {name for bundle in _PORTABLE_QUIESCENCE_SOURCE_BUNDLES for name in bundle}
            | {"services.machine_startup"}
        )
        - _PORTABLE_QUIESCENCE_CONDITIONAL_SOURCE_NAMES
    )
)


def _require_reviewed_portable_sources(sources: dict[str, str]) -> None:
    """Accept complete reviewed bundles, never hash-by-hash cross-products."""
    _kernel_need(
        type(sources) is dict
        and any(sources == bundle for bundle in _PORTABLE_QUIESCENCE_SOURCE_BUNDLES),
        "quiescence_unsupported_source",
    )


def _portable_quiescence_sources() -> dict[str, str]:
    """Match a producer first, then bind its complete conditional dependency set."""
    sources = {}

    def collect(names):
        for name in names:
            spec = importlib.util.find_spec("yleum_orchestrator." + name)
            if spec is None:
                continue
            _kernel_need(spec.origin is not None, "quiescence_source")
            sources[name] = hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest()

    collect(_PORTABLE_QUIESCENCE_SOURCE_NAMES)
    candidates = [
        bundle
        for bundle in _PORTABLE_QUIESCENCE_SOURCE_BUNDLES
        if {
            name: digest
            for name, digest in bundle.items()
            if name in _PORTABLE_QUIESCENCE_SOURCE_NAMES
        } == sources
    ]
    _kernel_need(bool(candidates), "quiescence_unsupported_source")
    # Existing schemas may predate the new producer. They become authority only
    # for the exact producer bundle that actually uses that dependency contract.
    collect(sorted({name for bundle in candidates for name in bundle} - sources.keys()))
    _require_reviewed_portable_sources(sources)
    return sources


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _secure_bytes(path: Path, uid: int, mode: int) -> bytes:
    """Root reads controller-owned state without changing its ownership."""
    _kernel_need(path.is_absolute(), "quiescence_path")
    for parent in [path.parent, *path.parents]:
        info = parent.lstat()
        _kernel_need(stat.S_ISDIR(info.st_mode), "quiescence_path")
        _kernel_need(info.st_uid in {0, uid}, "quiescence_owner")
        _kernel_need(
            not info.st_mode & 0o022
            or info.st_uid == 0
            and bool(info.st_mode & stat.S_ISVTX),
            "quiescence_path_mode",
        )
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        _kernel_need(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and info.st_uid == uid
            and stat.S_IMODE(info.st_mode) == mode,
            "quiescence_file_authority",
        )
        value = stream.read(8 * 1024 * 1024 + 1)
    _kernel_need(len(value) <= 8 * 1024 * 1024, "quiescence_file_bound")
    return value


@contextmanager
def _quiescence_lock(state_parent: Path, workspace_id: str):
    """The existing controller lock; never create/replace it as root."""
    import fcntl

    path = state_parent / "locks" / ("workspace-" + workspace_id + ".lock")
    owner = (state_parent / "project-cells" / (workspace_id + ".json")).lstat().st_uid
    _secure_bytes(path, owner, 0o600)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        expected = path.lstat()
        actual = os.fstat(fd)
        _kernel_need(
            (expected.st_dev, expected.st_ino) == (actual.st_dev, actual.st_ino)
            and actual.st_uid == owner
            and stat.S_ISREG(actual.st_mode)
            and actual.st_nlink == 1
            and stat.S_IMODE(actual.st_mode) == 0o600,
            "quiescence_lock_authority",
        )
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield owner
    finally:
        os.close(fd)


def _bind_pending_fast_check(
    machine: dict, request_id: str, record: dict, child: dict | None,
) -> None:
    """Verify the reviewed fast check before dispatch or at its typecheck child."""
    _kernel_need(
        set(record) == {
            "digest", "state", "epoch", "kind", "started_at", "heartbeat_at",
            "log_bytes", "phase", "startup_role", "deadline_at",
        }
        and record["state"] == "running"
        and record["startup_role"] == "fast_check"
        and str(UUID(request_id)) == request_id
        and type(record["epoch"]) is int
        and type(machine.get("epoch")) is int
        and type(machine.get("ready_epoch")) is int
        and record["epoch"] == machine["epoch"] == machine["ready_epoch"]
        and type(record["log_bytes"]) is int
        and record["log_bytes"] >= 0
        and type(record["digest"]) is str
        and re.fullmatch(r"[0-9a-f]{64}", record["digest"]),
        "quiescence_command_scope",
    )
    start, heartbeat, deadline = (
        datetime.fromisoformat(value) for value in (
            record["started_at"], record["heartbeat_at"], record["deadline_at"],
        )
    )
    _kernel_need(
        all(value.utcoffset() is not None for value in (start, heartbeat, deadline))
        and start <= heartbeat <= deadline
        and start < deadline,
        "quiescence_command_scope",
    )
    if child is None:
        # The reviewed adapter creates this request before the read-only
        # migration dependency check. It changes the phase before any child.
        _kernel_need(
            record["phase"] == "fast_check" and record["heartbeat_at"] == record["started_at"]
            and record["log_bytes"] == 0
            and type(machine.get("manifest")) is dict
            and type(machine["manifest"].get("tasks")) is list,
            "quiescence_command_scope",
        )
        return
    child_start = datetime.fromisoformat(child["started_at"])
    _kernel_need(
        record["phase"] == "typecheck" and child["epoch"] == record["epoch"]
        and child_start.utcoffset() is not None and heartbeat <= child_start <= deadline,
        "quiescence_command_scope",
    )
    manifest = machine.get("manifest")
    tasks = manifest.get("tasks") if type(manifest) is dict else None
    _kernel_need(type(tasks) is list, "quiescence_command_scope")
    matches = [
        task for task in tasks if type(task) is dict
        and task.get("name") == record["phase"] and task.get("role") == record["startup_role"]
    ]
    _kernel_need(len(matches) == 1, "quiescence_command_scope")
    task = matches[0]
    # The stored manifest has already passed the controller's command schema.
    # Reproduce the digest of the persisted child using those exact fields.
    argv, cwd = task.get("argv"), task.get("cwd")
    _kernel_need(
        type(argv) is list and bool(argv) and all(type(arg) is str for arg in argv)
        and type(cwd) is str and bool(cwd),
        "quiescence_command_scope",
    )
    digest = hashlib.sha256(json.dumps({
        "command": {"argv": argv, "cwd": cwd},
        "request": record["digest"],
        "epoch": record["epoch"],
    }, sort_keys=True).encode()).hexdigest()
    _kernel_need(child["digest"] == digest, "quiescence_command_scope")


def _pending_portable_commands(machine: dict, *, sources: dict[str, str]) -> dict[str, str]:
    """Bind exec intents and a reviewed wrapper on a retired development machine."""
    operations = machine.get("operations")
    _kernel_need(type(operations) is dict, "quiescence_command_journal")
    pending = {}
    requests = {}
    for operation_id, record in operations.items():
        _kernel_need(type(record) is dict, "quiescence_command_journal")
        if record.get("state") in {"completed", "failed", "timed_out"}:
            continue
        if record.get("kind") == "request":
            requests[operation_id] = record
            continue
        _kernel_need(
            set(record)
            == {
                "digest",
                "state",
                "epoch",
                "kind",
                "started_at",
                "heartbeat_at",
                "log_bytes",
            }
            and record["kind"] == "command"
            and record["state"] == "starting"
            and str(UUID(operation_id)) == operation_id
            and type(record["epoch"]) is int
            and record["epoch"] > 0
            and type(record["log_bytes"]) is int
            and record["log_bytes"] == 0
            and type(record["digest"]) is str
            and re.fullmatch(r"[0-9a-f]{64}", record["digest"]),
            "quiescence_command_scope",
        )
        start = datetime.fromisoformat(record["started_at"])
        heartbeat = datetime.fromisoformat(record["heartbeat_at"])
        _kernel_need(
            start.utcoffset() is not None and heartbeat == start,
            "quiescence_command_scope",
        )
        pending[operation_id] = _digest(record)
    if requests:
        _kernel_need(
            sources in (_PORTABLE_QUIESCENCE_SOURCE_GUARD_LEASE_SOURCES,
                        _PORTABLE_QUIESCENCE_READER_WAIT_SOURCES,
                        _PORTABLE_QUIESCENCE_MEAL_ACCEPTANCE_SOURCES)
            and len(requests) == 1 and len(pending) <= 1,
            "quiescence_command_scope",
        )
        ((request_id, request),) = requests.items()
        child = None
        if pending:
            (child_id,) = pending
            _kernel_need(
                type(request.get("phase")) is str
                and str(uuid5(UUID(request_id), request["phase"])) == child_id,
                "quiescence_command_scope",
            )
            child = operations[child_id]
        _bind_pending_fast_check(machine, request_id, request, child)
        if child is None:
            # The pinned producer derives child IDs from manifest tasks or
            # these reserved preflights. Check even already-terminal children.
            names = {"controller:next-type-preparation:v1", "controller:react-effect-lifetime:v1"}
            tasks = machine["manifest"]["tasks"]
            _kernel_need(
                0 < len(tasks) <= 1000 and all(
                    type(task) is dict and type(task.get("name")) is str
                    and re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", task["name"])
                    for task in tasks
                ) and any(task.get("role") == "fast_check" for task in tasks),
                "quiescence_command_scope",
            )
            names.update(task["name"] for task in tasks)
            _kernel_need(
                not any(str(uuid5(UUID(request_id), name)) in operations for name in names),
                "quiescence_command_scope",
            )
        pending[request_id] = _digest(request)
    _kernel_need(bool(pending), "quiescence_command_scope")
    return pending


def _daemon_identity(client) -> dict:
    info = client.info()
    _kernel_need(
        type(info.get("ID")) is str
        and bool(info["ID"])
        and info.get("CgroupDriver") == "systemd"
        and info.get("CgroupVersion") == "2",
        "quiescence_daemon_contract",
    )
    return {key: info[key] for key in ("ID", "CgroupDriver", "CgroupVersion")}


def _reconciliation_digest(workspace_id: str, target_id: UUID) -> str:
    # Match the platform's canonical ownerless Project Cell operation envelope.
    return _digest({
        "workspace_id": workspace_id, "generation_run_id": None, "kind": "reconcile",
        "request": {"indeterminate_operation_id": str(target_id)},
    })


def _reconciled_restore_retirement(rows, operation, workspace_id: str) -> bool:
    """Allow current physical proof after an interrupted idle restore was observed."""
    if operation.kind != "reconcile" or operation.bundle_state != "resources_paused":
        return False
    if len(rows) < 3:
        return False
    # Preserve the preceding canonical pause, and require one exact newer
    # observation for every uncertain restore/observation in this tail.
    pauses = [i for i, row in enumerate(rows[:-1]) if row.kind == "pause"]
    if not pauses:
        return False
    pause_index = pauses[-1]
    pause = rows[pause_index]
    if (
        pause.status != "completed" or pause.phase != "completed"
        or pause.bundle_state != "resources_paused" or pause.generation_run_id is not None
        or not pause.checkpoint_ref
        or pause.request_digest != _digest({
            "workspace_id": workspace_id, "generation_run_id": None, "kind": "pause",
            "request": {"checkpoint_ref": pause.checkpoint_ref},
        })
        or any(row.status not in {"completed", "failed", "cancelled"}
               or row.fencing_epoch > pause.fencing_epoch for row in rows[:pause_index])
    ):
        return False
    tail = rows[pause_index + 1:]
    restore = tail[0]
    if (
        restore.kind != "restore" or restore.status != "indeterminate"
        or restore.phase != "completed" or restore.bundle_state != "degraded"
        or restore.checkpoint_ref != pause.checkpoint_ref
        or restore.request_digest != _digest({
            "workspace_id": workspace_id, "generation_run_id": None, "kind": "restore",
            "request": {"checkpoint_ref": pause.checkpoint_ref},
        })
        or any(row.generation_run_id is not None for row in tail)
        or any(prev.fencing_epoch >= row.fencing_epoch
               for prev, row in zip((pause, *tail[:-1]), tail, strict=True))
        or len({row.operation_id for row in rows}) != len(rows)
    ):
        return False
    unresolved = {_reconciliation_digest(workspace_id, restore.operation_id)}
    for row in tail[1:]:
        if row.kind != "reconcile" or row.checkpoint_ref is not None:
            return False
        if row.request_digest not in unresolved:
            return False
        if row.status == "indeterminate":
            if (row.phase, row.bundle_state) != ("indeterminate", "indeterminate"):
                return False
            unresolved.add(_reconciliation_digest(workspace_id, row.operation_id))
        elif (row.status, row.phase, row.bundle_state) == (
            "completed", "completed", "resources_paused",
        ):
            unresolved.remove(row.request_digest)
        else:
            return False
    return not unresolved


def _retirement_operation(cell, workspace_id: str):
    """Bind a canonical pause/destroy or a fully observed idle restore tail."""
    operation = cell.operation(cell.last_operation_id)
    _kernel_need(
        str(cell.workspace_id) == workspace_id
        and cell.project_id is not None
        and cell.owner_id is not None
        and cell.phase == "completed"
        and cell.active_generation_run_id is None
        and cell.active_generation_fencing_epoch is None
        and operation is not None
        and operation.status == "completed"
        and operation.phase == "completed"
        and operation.bundle_state == cell.bundle_state
        and operation.fencing_epoch == cell.fencing_epoch
        and operation.generation_run_id is None
        and cell.operations[-1] == operation
        and re.fullmatch(r"[0-9a-f]{64}", operation.request_digest),
        "quiescence_retirement_authority",
    )
    rows = cell.operations
    completed_retirement = (
        (operation.kind, cell.bundle_state) in {("pause", "resources_paused"), ("destroy", "retained")}
        and all(item.status in {"completed", "failed", "cancelled"}
                and item.fencing_epoch <= cell.fencing_epoch for item in rows)
    )
    destroyed_after_reconciliation = (
        operation.kind == "destroy"
        and cell.bundle_state == "retained"
        and len(rows) >= 4
        and rows[-2].fencing_epoch < operation.fencing_epoch
        and operation.operation_id not in {item.operation_id for item in rows[:-1]}
        # Owner-canary destroy writes a new checkpoint bound to this mutation.
        and operation.checkpoint_ref == f"final-{operation.fencing_epoch}-{operation.operation_id.hex}"
        and operation.request_digest == _digest({
            "workspace_id": workspace_id, "generation_run_id": None, "kind": "destroy",
            "request": {},
        })
        and _reconciled_restore_retirement(rows[:-1], rows[-2], workspace_id)
    )
    _kernel_need(
        completed_retirement or destroyed_after_reconciliation
        or _reconciled_restore_retirement(rows, operation, workspace_id),
        "quiescence_retirement_authority",
    )
    return operation


def _quiescence_binding(state_parent: Path, workspace_id: str, uid: int, client):
    from yleum_orchestrator.services.cell_state import CellWorkspaceState

    cell_raw = _secure_bytes(
        state_parent / "project-cells" / (workspace_id + ".json"), uid, 0o600
    )
    envelope = json.loads(cell_raw)
    _kernel_need(
        set(envelope) == {"version", "workspace"}
        and type(envelope["version"]) is int
        and envelope["version"] == 1,
        "quiescence_cell_schema",
    )
    cell = CellWorkspaceState.from_dict(envelope["workspace"])
    operation = _retirement_operation(cell, workspace_id)
    directory = state_parent / "project-machines" / workspace_id
    machine_raw = _secure_bytes(directory / "machine.json", uid, 0o600)
    backend_raw = _secure_bytes(directory / "docker.json", uid, 0o600)
    machine = json.loads(machine_raw)
    backend = json.loads(backend_raw)
    sources = _portable_quiescence_sources()
    pending = _pending_portable_commands(machine, sources=sources)
    _kernel_need(
        machine.get("workspace_id") == workspace_id
        and type(machine.get("epoch")) is int
        and 0 < machine["epoch"] < cell.fencing_epoch
        and type(backend.get("epoch")) is int
        and backend["epoch"] == machine["epoch"]
        and all(
            machine["operations"][key]["epoch"] <= machine["epoch"] for key in pending
        ),
        "quiescence_retirement_fence",
    )
    binding = {
        "protocol": "portable-child-command-quiescence",
        "version": 1,
        "state_parent": str(state_parent.resolve()),
        "workspace_id": workspace_id,
        "project_id": str(cell.project_id),
        "owner_id": str(cell.owner_id),
        operation.kind + "_operation_id": str(operation.operation_id),
        "request_digest": operation.request_digest,
        "fencing_epoch": cell.fencing_epoch,
        "cell_sha256": hashlib.sha256(cell_raw).hexdigest(),
        "machine_sha256": hashlib.sha256(machine_raw).hexdigest(),
        "backend_sha256": hashlib.sha256(backend_raw).hexdigest(),
        "commands": pending,
        "boot_id": _kernel_boot_id(),
        "daemon": _daemon_identity(client),
        "sources_sha256": sources,
    }
    startup_prepares = _startup_prepare_journals(state_parent, workspace_id)
    if startup_prepares:
        # Preserve historical empty bindings. New terminal history is an exact
        # immutable input, rechecked under this same lock before publication.
        binding["startup_prepares_sha256"] = startup_prepares
    return binding, cell


def _docker_cgroup_id(parts) -> str | None:
    found = []
    for part in parts:
        match = re.fullmatch(r"docker-([0-9a-f]{64})\.scope", part)
        if match:
            found.append(match[1])
        elif (
            part.startswith("docker-")
            or part == "docker"
            or re.fullmatch(r"[0-9a-f]{64}", part)
        ):
            raise PhysicalDrainError("quiescence_cgroup_contract")
    _kernel_need(len(found) <= 1, "quiescence_nested_container")
    return found[0] if found else None


def _process_memberships(process: Path, inventory_reason: str):
    """Read every member under exact PID/start proof; never skip a disappearing row."""
    _kernel_need(not process.is_symlink(), "process_path")
    with _observed_process(process):
        start = _process_start(process)
        tasks = list((process / "task").iterdir())
    _kernel_need(any(task.name == process.name for task in tasks), inventory_reason)
    for member in [process, *tasks]:
        _kernel_need(member.name.isdecimal() and not member.is_symlink(), "process_path")
        with _observed_process(member):
            member_start = _process_start(member)
            membership = _kernel_read(member / "cgroup").decode("ascii").strip()
            _kernel_need(
                _process_start(member) == member_start, "quiescence_process_identity_changed"
            )
        _kernel_need(
            membership.startswith("0::/") and "\n" not in membership, "cgroup_v2_required"
        )
        yield membership
    with _observed_process(process):
        _kernel_need(_process_start(process) == start, "quiescence_process_identity_changed")


def _require_no_orphan_docker_processes(known_ids: set[str]) -> dict:
    """Complete host scope, including tasks and nested membership; root only."""
    boot = _kernel_boot_id()
    _kernel_need(_CGROUP_ROOT.is_dir() and not _CGROUP_ROOT.is_symlink(), "cgroup_root")
    pending = [_CGROUP_ROOT]
    while pending:
        directory = pending.pop()
        identity = _docker_cgroup_id(directory.relative_to(_CGROUP_ROOT).parts)
        _kernel_need(
            identity is None or identity in known_ids, "quiescence_orphan_cgroup"
        )
        # Read all membership, including empty/non-Docker groups: unreadable or
        # disappearing paths cannot establish a complete host observation.
        _kernel_ids(directory / "cgroup.procs")
        _kernel_ids(directory / "cgroup.threads")
        for entry in directory.iterdir():
            _kernel_need(not entry.is_symlink(), "cgroup_symlink")
            if entry.is_dir():
                pending.append(entry)
    for process in _PROC_ROOT.iterdir():
        if not process.name.isdecimal():
            continue
        for membership in _process_memberships(process, "quiescence_task_inventory"):
            identity = _docker_cgroup_id(Path(membership[3:]).parts)
            _kernel_need(
                identity is None or identity in known_ids, "quiescence_orphan_process"
            )
    _kernel_need(_kernel_boot_id() == boot, "host_boot_changed")
    return {"boot_id": boot, "no_orphan_docker_membership": True}


@contextmanager
def _observed_process(directory: Path):
    """Missing membership in a live directory is not evidence of process exit."""
    try:
        yield
    except FileNotFoundError:
        try:
            directory.lstat()
        except FileNotFoundError:
            raise ProcessDisappeared("quiescence_process_disappeared") from None
        raise


def _process_start(directory: Path) -> int:
    raw = _kernel_read(directory / "stat")
    pid, separator, rest = raw.partition(b" (")
    _comm, closing, suffix = rest.rpartition(b") ")
    fields = suffix.split()
    _kernel_need(
        separator and closing and pid == directory.name.encode("ascii") and len(fields) >= 20
        and fields[0] in (b"R", b"S", b"D", b"Z", b"T", b"t", b"X", b"x", b"K", b"W", b"P", b"I")
        and fields[19].isdigit(),
        "quiescence_process_stat",
    )
    return int(fields[19])


def _quiescent_roster(client, binding, cell) -> dict:
    containers = client.containers.list(all=True)
    roster = {}
    auxiliary = (
        {
            cell.resource_names.postgres_container: "postgres",
            cell.resource_names.redis_container: "redis",
        }
        if cell.resource_names is not None and cell.bundle_state == "resources_paused"
        else {}
    )
    destroyed_names = (
        {
            cell.resource_names.postgres_container,
            cell.resource_names.redis_container,
            cell.resource_names.draft_container_name(),
        }
        if cell.resource_names is not None and cell.bundle_state == "retained"
        else set()
    )
    for container in containers:
        container.reload()
        attrs = container.attrs
        _kernel_need(
            re.fullmatch(r"[0-9a-f]{64}", container.id)
            and attrs.get("Id") == container.id,
            "quiescence_container_id",
        )
        labels = attrs.get("Config", {}).get("Labels") or {}
        _kernel_need(
            container.name not in destroyed_names,
            "quiescence_workspace_writer_present",
        )
        # A corrupt/missing identity must not hide a machine with its canonical name.
        target = labels.get("omnia.workspace_id") == binding["workspace_id"]
        named = (
            "omnia-machine-" + binding["workspace_id"].replace("-", "")
        ) in container.name
        _kernel_need(not named, "quiescence_machine_present")
        if target:
            _kernel_need(
                container.name in auxiliary
                and labels.get("omnia.project_cell") == "true"
                and labels.get("omnia.resource_kind") == auxiliary[container.name]
                and labels.get("omnia.project_machine") != "true"
                and labels.get("omnia.helper") != "true"
                and all(
                    labels.get("omnia." + key) == binding[key]
                    for key in ("workspace_id", "project_id", "owner_id")
                ),
                "quiescence_workspace_writer_present",
            )
            exec_ids = attrs.get("ExecIDs")
            _kernel_need(
                exec_ids is None or type(exec_ids) is list,
                "quiescence_auxiliary_exec_inventory",
            )
            for exec_id in exec_ids or []:
                _kernel_need(
                    type(exec_id) is str and re.fullmatch(r"[0-9a-f]{64}", exec_id),
                    "quiescence_auxiliary_exec_identity",
                )
                execution = client.api.exec_inspect(exec_id)
                _kernel_need(
                    execution.get("ContainerID") == container.id
                    and execution.get("Running") is False
                    and type(execution.get("ExitCode")) is int,
                    "quiescence_auxiliary_exec",
                )
        roster[container.id] = {"name": container.name, "labels": labels}
    return roster


def _quiescence_physical(state_parent, workspace_id, uid, client, binding, cell):
    # Discard the entire attempt, including the first successful scan and its
    # Docker roster. Never retain partial evidence or retry an unknown live file.
    for attempt in range(5):
        try:
            return _quiescence_physical_attempt(
                state_parent, workspace_id, uid, client, binding, cell
            )
        except ProcessDisappeared:
            if attempt == 4:
                raise
    raise AssertionError("unreachable")


def _quiescence_physical_attempt(state_parent, workspace_id, uid, client, binding, cell):
    roster = _quiescent_roster(client, binding, cell)
    # Two affirmative complete scans, not equality of unrelated host process
    # topology. Both independently reject every orphan Docker membership.
    first = _require_no_orphan_docker_processes(set(roster))
    second = _require_no_orphan_docker_processes(set(roster))
    _kernel_need(
        first["boot_id"] == second["boot_id"]
        and roster == _quiescent_roster(client, binding, cell),
        "quiescence_inventory_changed",
    )
    current, _ = _quiescence_binding(state_parent, workspace_id, uid, client)
    _kernel_need(current == binding, "quiescence_binding_changed")
    return {**first, "roster_sha256": _digest(roster)}


def _quiescence_receipt_path(binding):
    return _QUIESCENCE_ROOT / binding["workspace_id"] / (_digest(binding) + ".json")


def _ensure_root_quiescence_directory(path: Path) -> None:
    """Initialize missing storage without chmod/chown of existing paths."""
    for directory in reversed([path, *path.parents]):
        try:
            info = directory.lstat()
        except FileNotFoundError:
            directory.mkdir(mode=0o700)
            fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            info = directory.lstat()
        private = directory in {path, _QUIESCENCE_ROOT}
        _kernel_need(
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == 0
            and (
                stat.S_IMODE(info.st_mode) == 0o700
                if private
                else not info.st_mode & 0o022 or bool(info.st_mode & stat.S_ISVTX)
            ),
            "quiescence_receipt_directory",
        )


def machine_quiescence(state_parent, client, workspace_id, *, record=False):
    """A separate root attestation of current retirement, never a command result.

    Admission closure must span this operation and reader capture. The exact
    retired state is mandatory on EVERY use; wake/ensure invalidates this proof.
    """
    _kernel_need(os.geteuid() == 0, "quiescence_requires_root")
    _kernel_need(str(UUID(workspace_id)) == workspace_id, "quiescence_workspace_id")
    with _quiescence_lock(state_parent, workspace_id) as uid:
        binding, cell = _quiescence_binding(state_parent, workspace_id, uid, client)
        path = _quiescence_receipt_path(binding)
        if not record:
            payload = json.loads(_secure_bytes(path, 0, 0o600))
            _kernel_need(
                set(payload) == {"binding", "observed_at", "physical"}
                and payload["binding"] == binding
                and set(payload["physical"])
                == {"boot_id", "no_orphan_docker_membership", "roster_sha256"}
                and payload["physical"]["boot_id"] == binding["boot_id"]
                and payload["physical"]["no_orphan_docker_membership"] is True
                and re.fullmatch(r"[0-9a-f]{64}", payload["physical"]["roster_sha256"])
                and datetime.fromisoformat(payload["observed_at"]).utcoffset()
                is not None,
                "quiescence_receipt_invalid",
            )
        physical = _quiescence_physical(
            state_parent, workspace_id, uid, client, binding, cell
        )
        if record:
            _ensure_root_quiescence_directory(path.parent)
            payload = {
                "binding": binding,
                "observed_at": datetime.now(UTC).isoformat(),
                "physical": physical,
            }
            if path.exists():
                existing = json.loads(_secure_bytes(path, 0, 0o600))
                _kernel_need(
                    existing.get("binding") == binding, "quiescence_receipt_collision"
                )
            else:
                fd, temporary = tempfile.mkstemp(prefix=".quiescence-", dir=path.parent)
                try:
                    with os.fdopen(fd, "w") as stream:
                        json.dump(
                            payload, stream, sort_keys=True, separators=(",", ":")
                        )
                        stream.flush()
                        os.fsync(stream.fileno())
                    # Publish once, without replacing a prior attestation. A
                    # crash before this point leaves no partial final receipt.
                    os.link(temporary, path, follow_symlinks=False)
                finally:
                    os.unlink(temporary)
                for directory in (path.parent, _QUIESCENCE_ROOT):
                    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
        return binding["commands"]


def _kernel_need(condition, reason):
    if not condition:
        raise PhysicalDrainError(reason)


def _kernel_read(path: Path) -> bytes:
    with path.open("rb") as handle:
        value = handle.read(1048577)
    _kernel_need(len(value) <= 1048576, "kernel_read_bound")
    return value


def _kernel_ids(path: Path) -> set[int]:
    values = _kernel_read(path).decode("ascii").split()
    _kernel_need(all(v.isdecimal() and int(v) > 0 for v in values), "kernel_id_shape")
    return {int(v) for v in values}


def _kernel_status(path: Path) -> dict[str, str]:
    rows = _kernel_read(path).decode("ascii").splitlines()
    return dict(row.split(":", 1) for row in rows if ":" in row)


def _kernel_boot_id() -> str:
    value = (
        _kernel_read(_PROC_ROOT / "sys/kernel/random/boot_id").decode("ascii").strip()
    )
    _kernel_need(str(UUID(value)) == value, "host_boot_identity")
    return value


def _kernel_row(proc: Path, tid: int, group: str, namespace: int, allowed_caps: int):
    root = proc / str(tid)
    raw = _kernel_read(root / "stat").decode("ascii")
    fields = raw.rsplit(")", 1)[1].split()
    info = _kernel_status(root / "status")
    _kernel_need(fields[0] not in {"Z", "X", "x"}, "process_not_live")
    _kernel_need(int(info["Pid"]) == tid and int(fields[19]) > 0, "process_identity")
    _kernel_need(
        _kernel_read(root / "cgroup").decode("ascii").strip() == "0::" + group,
        "process_cgroup",
    )
    _kernel_need((root / "ns/pid").stat().st_ino == namespace, "process_namespace")
    _kernel_need(int(info["NoNewPrivs"]) == 1, "process_privileges")
    for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        _kernel_need(
            int(info[key].strip(), 16) & ~allowed_caps == 0, "process_capabilities"
        )
    nspid = tuple(map(int, info["NSpid"].split()))
    _kernel_need(nspid and nspid[0] == tid and nspid[-1] > 0, "process_namespace_pid")
    return {
        "pid": tid,
        "parent": int(fields[1]),
        "start": int(fields[19]),
        "tgid": int(info["Tgid"]),
        "nspid": nspid,
        "cmd": hashlib.sha256(_kernel_read(root / "cmdline")).hexdigest(),
        "exe": str((root / "exe").readlink()),
        "namespace": namespace,
        "group": group,
    }


def _kernel_snapshot(pid: int, *, proc=Path("/proc"), cgroups=Path("/sys/fs/cgroup")):
    group_line = _kernel_read(proc / str(pid) / "cgroup").decode("ascii").strip()
    _kernel_need(
        group_line.startswith("0::/") and "\n" not in group_line, "cgroup_v2_required"
    )
    group = group_line[3:]
    _kernel_need(
        group != "/" and ".." not in Path(group).parts, "container_cgroup_required"
    )
    root = cgroups / group.lstrip("/")
    _kernel_need(root.resolve() == root and root.is_dir(), "cgroup_path")
    # Unknown nested delegation is not treated as an empty container.
    _kernel_need(not any(p.is_dir() for p in root.iterdir()), "nested_cgroup_unknown")
    processes, threads = (
        _kernel_ids(root / "cgroup.procs"),
        _kernel_ids(root / "cgroup.threads"),
    )
    _kernel_need(pid in processes and processes <= threads, "init_cgroup_membership")
    namespace = (proc / str(pid) / "ns/pid").stat().st_ino
    _kernel_need(
        namespace != (proc / "1/ns/pid").stat().st_ino, "host_namespace_denied"
    )
    # Exact capabilities from DockerMachineBackend._USERLAND_CAPS.
    allowed_caps = sum(1 << bit for bit in (0, 1, 3, 4, 6, 7, 31, 5, 10, 18))
    rows = {}
    for process in sorted(processes):
        task_ids = {int(p.name) for p in (proc / str(process) / "task").iterdir()}
        _kernel_need(process in task_ids and task_ids <= threads, "task_inventory")
        _kernel_need(process != pid or task_ids == {pid}, "idle_init_tasks")
        for tid in sorted(task_ids):
            _kernel_need(tid not in rows, "duplicate_task")
            item = _kernel_row(proc, tid, group, namespace, allowed_caps)
            _kernel_need(item["tgid"] == process, "task_group")
            rows[tid] = item
    _kernel_need(set(rows) == threads, "unaccounted_thread")
    _kernel_need(rows[pid]["nspid"][-1] == 1, "container_init_pid")
    _kernel_need(
        processes == _kernel_ids(root / "cgroup.procs")
        and threads == _kernel_ids(root / "cgroup.threads"),
        "kernel_inventory_changed",
    )
    return {
        "boot": _kernel_boot_id(),
        "group": group,
        "namespace": namespace,
        "processes": sorted(processes),
        "rows": rows,
    }


def _service_forest(snapshot, roots: set[int], init: int):
    processes = set(snapshot["processes"])
    _kernel_need(roots <= processes and init not in roots, "service_root_missing")
    for pid in processes - {init}:
        seen, parent = set(), pid
        while parent not in roots:
            _kernel_need(
                parent in processes and parent != init and parent not in seen,
                "unknown_process",
            )
            seen.add(parent)
            parent = snapshot["rows"][parent]["parent"]


def _stopped_kernel_absence(container_id: str):
    """Default Docker cgroup namespace only; any matching residual denies.

    Read every cgroup directory and process membership, including nested groups.
    Only a proven process disappearance permits a fresh complete attempt.
    Other failed/racing reads cannot establish absence. No process is signalled.
    """
    boot = _kernel_boot_id()
    names = {container_id, "docker-" + container_id + ".scope"}
    _kernel_need(_CGROUP_ROOT.is_dir() and not _CGROUP_ROOT.is_symlink(), "cgroup_root")
    pending = [_CGROUP_ROOT]
    while pending:
        directory = pending.pop()
        _kernel_need(directory.name not in names, "stopped_cgroup_present")
        for entry in directory.iterdir():
            _kernel_need(not entry.is_symlink(), "cgroup_symlink")
            if entry.is_dir():
                pending.append(entry)
    for process in _PROC_ROOT.iterdir():
        if not process.name.isdecimal():
            continue
        for membership in _process_memberships(process, "stopped_task_inventory"):
            _kernel_need(
                not (set(Path(membership[3:]).parts) & names), "stopped_process_present"
            )
    _kernel_need(_kernel_boot_id() == boot, "host_boot_changed")
    return {"boot": boot, "stopped_absent": True}


def _missing_service_execs(client, services: set[str]) -> tuple[str, ...]:
    """Current exact Docker404 only; never infer a service's historical outcome."""
    from docker.errors import NotFound

    _kernel_need(
        all(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) for value in services),
        "service_exec_id",
    )
    missing = tuple(sorted(services))
    for exec_id in missing:
        try:
            client.api.exec_inspect(exec_id)
        except Exception as exc:
            _kernel_need(
                type(exc) is NotFound and exc.status_code == 404,
                "service_exec_absence_unconfirmed",
            )
        else:
            raise PhysicalDrainError("service_exec_absence_unconfirmed")
    return missing


def _command_absence_sample(client, state_parent, container):
    from yleum_orchestrator.services.docker_machine_backend import (
        _SERVICE_WRAPPER,
        _USERLAND_CAPS,
    )

    container.reload()
    attrs = copy.deepcopy(container.attrs)
    labels = attrs.get("Config", {}).get("Labels") or {}
    _kernel_need(
        labels.get("omnia.managed") == "true"
        and labels.get("omnia.project_machine") == "true"
        and labels.get("omnia.resource_kind") == "development",
        "container_owner_labels",
    )
    for key in ("workspace_id", "project_id", "owner_id"):
        value = labels.get("omnia." + key)
        _kernel_need(
            type(value) is str and str(UUID(value)) == value, "container_owner_identity"
        )
    config, host, state = attrs["Config"], attrs["HostConfig"], attrs["State"]
    _kernel_need(
        re.fullmatch(r"[0-9a-f]{64}", container.id) and attrs["Id"] == container.id,
        "container_id",
    )
    _kernel_need(
        state.get("Paused") is False
        and state.get("Dead") is False
        and state.get("Restarting") is False
        and type(state.get("Pid")) is int
        and bool(state.get("StartedAt")),
        "container_state",
    )
    _kernel_need(
        host.get("Privileged") is False
        and host.get("PidMode") in ("", None)
        and host.get("UsernsMode") in ("", None)
        and host.get("CgroupnsMode") == "private"
        and set(host.get("CapDrop") or []) == {"ALL"}
        and set(host.get("CapAdd") or []) == set(_USERLAND_CAPS)
        and host.get("SecurityOpt") == ["no-new-privileges:true"],
        "container_security",
    )
    idle = "import signal,sys; signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); signal.pause()"
    _kernel_need(
        config.get("Entrypoint") == ["python3", "-c"]
        and config.get("Cmd") == [idle]
        and config.get("User") == "0:0"
        and host.get("Init") in (None, False),
        "idle_init_config",
    )
    common = {
        "id": container.id,
        "state": state,
        "exec_ids": attrs.get("ExecIDs"),
        "config": hashlib.sha256(
            json.dumps({"config": config, "host": host}, sort_keys=True).encode()
        ).hexdigest(),
    }
    services, _ = _machine_execs(state_parent, labels)
    fresh_exec_ids = attrs.get("ExecIDs")
    # Docker serializes an empty exec store as null. A running container must
    # still prove every recorded service missing and have only its idle init;
    # no result is inferred for historical execs. Preserve raw null for comparison.
    _kernel_need(
        "ExecIDs" in attrs
        and (
            type(fresh_exec_ids) is list
            or fresh_exec_ids is None
            and state.get("Status") in {"exited", "running"}
        ),
        "fresh_exec_inventory",
    )
    fresh_execs = {}
    for exec_id in fresh_exec_ids or []:
        _kernel_need(
            type(exec_id) is str and re.fullmatch(r"[0-9a-f]{64}", exec_id),
            "fresh_exec_identity",
        )
        execution = client.api.exec_inspect(exec_id)
        terminal = (
            execution.get("Running") is False and type(execution.get("ExitCode")) is int
        )
        live_service = (
            state.get("Status") == "running"
            and exec_id in services
            and execution.get("Running") is True
            and execution.get("ExitCode") is None
        )
        _kernel_need(
            execution.get("ContainerID") == container.id and (terminal or live_service),
            "fresh_exec_unresolved",
        )
        fresh_execs[exec_id] = {
            key: execution.get(key)
            for key in ("ContainerID", "Running", "ExitCode", "Pid")
        }
    common["fresh_execs"] = fresh_execs
    if state.get("Status") == "exited":
        _kernel_need(
            state.get("Running") is False
            and state["Pid"] == 0
            and host.get("RestartPolicy", {}).get("Name") == "no"
            and host.get("CgroupParent") == "",
            "stopped_container_state",
        )
        return {**common, "kernel": _stopped_kernel_absence(container.id)}
    _kernel_need(
        state.get("Status") == "running"
        and state.get("Running") is True
        and state["Pid"] > 0,
        "container_state",
    )
    pid = state["Pid"]
    _kernel_need(
        _kernel_read(_PROC_ROOT / str(pid) / "cmdline").split(b"\0")[:-1]
        == [b"python3", b"-c", idle.encode()],
        "idle_init_process",
    )
    metadata = _journal(
        state_parent / "project-machines" / labels["omnia.workspace_id"] / "docker.json"
    )
    declared = {item["name"]: item for item in metadata["manifest"]["services"]}
    expected_arguments = {
        record["exec_id"]: [
            declared[name]["argv"],
            "/workspace/" + declared[name]["cwd"],
            record["log"],
            declared[name]["restart"],
        ]
        for name, record in metadata["services"].items()
    }
    live_services = services
    missing_services = ()
    if fresh_exec_ids is None and services:
        common["daemon"] = _daemon_identity(client)
        missing_services = _missing_service_execs(client, services)
        common["missing_service_execs"] = missing_services
        live_services = set()
    execs, roots = {}, set()
    for exec_id in live_services:
        _kernel_need(re.fullmatch(r"[0-9a-f]{64}", exec_id), "service_exec_id")
        execution = client.api.exec_inspect(exec_id)
        process = execution.get("ProcessConfig") or {}
        arguments = process.get("arguments")
        _kernel_need(
            execution.get("ContainerID") == container.id
            and execution.get("Running") is True
            and execution.get("ExitCode") is None
            and type(execution.get("Pid")) is int
            and execution["Pid"] > 0
            and process.get("entrypoint") == "python3"
            and isinstance(arguments, list)
            and len(arguments) == 3
            and arguments[:2] == ["-c", _SERVICE_WRAPPER],
            "live_service_binding",
        )
        _kernel_need(
            json.loads(arguments[2]) == expected_arguments[exec_id],
            "service_manifest_binding",
        )
        root = execution["Pid"]
        _kernel_need(root not in roots, "duplicate_service_root")
        roots.add(root)
        _kernel_need(
            _kernel_read(_PROC_ROOT / str(root) / "cmdline").split(b"\0")[:-1]
            == [b"python3", *[v.encode() for v in arguments]],
            "live_service_process",
        )
        execs[exec_id] = {
            k: execution.get(k)
            for k in ("ContainerID", "Running", "ExitCode", "Pid", "ProcessConfig")
        }
    snapshot = _kernel_snapshot(pid, proc=_PROC_ROOT, cgroups=_CGROUP_ROOT)
    _kernel_need(
        bool(
            set(Path(snapshot["group"]).parts)
            & {container.id, "docker-" + container.id + ".scope"}
        ),
        "container_cgroup_identity",
    )
    _service_forest(snapshot, roots, pid)
    if missing_services:
        _kernel_need(
            _missing_service_execs(client, services) == missing_services
            and _daemon_identity(client) == common["daemon"],
            "service_absence_binding_changed",
        )
    return {**common, "metadata": metadata, "execs": execs, "kernel": snapshot}


def _require_current_command_absence(
    state_parent: Path, client: object, containers: list
) -> None:
    """Current sampled absence only, never evidence of an old exec outcome.

    Called solely after strict terminal journal validation and exact Docker404.
    External admission/ingress closure must span this read-only sample.
    """
    for attempt in range(5):
        try:
            # Retry only a proven exit, discarding BOTH samples and every Docker/
            # cgroup observation. A missing live entry or PID reuse is not an exit.
            first = [_command_absence_sample(client, state_parent, c) for c in containers]
            second = [_command_absence_sample(client, state_parent, c) for c in containers]
            _kernel_need(first == second, "current command absence sample changed")
            return
        except ProcessDisappeared:
            if attempt == 4:
                raise
        except PhysicalDrainError:
            raise
        except Exception as exc:
            raise PhysicalDrainError("current command absence is unknown") from exc


def _journal(path: Path) -> dict | None:
    if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
        raise PhysicalDrainError("physical journal path is unsafe")
    try:
        value = json.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        raise PhysicalDrainError("physical journal shape is unknown")
    return value


def _machine_execs(state_parent: Path, labels: dict) -> tuple[set[str], set[str]]:
    """Only persisted declared runtime services are outside generation drain."""
    root = state_parent / "project-machines" / labels["omnia.workspace_id"]
    machine = _journal(root / "machine.json") or {}
    commands = {
        record["exec_id"]
        for record in machine.get("operations", {}).values()
        if isinstance(record, dict)
        and record.get("kind") == "command"
        and record.get("exec_id")
    }
    if labels.get("omnia.resource_kind") != "development":
        return set(), commands
    metadata = _journal(root / "docker.json")
    if metadata is None:
        return set(), commands
    epoch = metadata.get("epoch")
    if type(epoch) is not int or labels.get("omnia.fencing_epoch") != str(epoch):
        raise PhysicalDrainError("physical service epoch is unknown")
    declared = {
        item["name"] for item in metadata.get("manifest", {}).get("services", [])
    }
    services = set()
    for name, record in metadata.get("services", {}).items():
        if name not in declared or record.get("epoch") != epoch:
            raise PhysicalDrainError("physical service binding is unknown")
        services.add(record["exec_id"])
    if services & (commands | set(metadata.get("exec_logs", {}))):
        raise PhysicalDrainError("physical service conflicts with command journal")
    return services, commands


def _terminal_record(value: dict, family: str, path: Path) -> None:
    """Reuse real typed journal contracts, without their mutating load/fsync paths."""
    try:
        if not isinstance(value.get("receipt"), dict):
            raise ValueError("pending")
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
        if family == "source-write-operations":
            from yleum_orchestrator.services.source_write_journal import (
                SourceWriteRecord,
            )

            record = SourceWriteRecord.model_validate_json(raw, strict=True)
            binding = record
            if (
                record.receipt is None
                or record.receipt.model_dump(
                    exclude={"after_revision", "written", "deleted"}
                )
                != record.binding()
            ):
                raise ValueError("binding")
        else:
            from yleum_orchestrator.services.command_receipts import (
                CommandJournal,
                CommandRecord,
            )

            record = CommandRecord.model_validate_json(raw, strict=True)
            binding = record.binding
            response = record.response
            if (
                response is None
                or record.receipt != CommandJournal._receipt(binding, response)
                or response.operation_id != binding.operation_id
                or response.before_identity.workspace_revision
                != binding.before_revision
                or response.after_identity.workspace_revision
                != response.workspace_revision
            ):
                raise ValueError("binding")
        if (
            str(binding.workspace_id) != path.parent.name
            or str(binding.operation_id) != path.stem
            or json.dumps(
                record.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            )
            != raw
        ):
            raise ValueError("canonical identity")
    except Exception as exc:
        raise PhysicalDrainError(
            "typed physical terminal receipt is unresolved"
        ) from exc


def _command_terminal(record: dict, operation_id: str) -> str:
    try:
        from yleum_orchestrator.services.project_machine import MachineOperationResult

        result = MachineOperationResult.model_validate_json(
            json.dumps(record["result"]), strict=True
        )
        if (
            str(UUID(operation_id)) != operation_id
            or result.operation_id != operation_id
            or result.state != record["state"]
            or type(result.exit_code) is not int
            or not re.fullmatch(r"[0-9a-f]{64}", record["exec_id"])
        ):
            raise ValueError("command")
        return record["exec_id"]
    except Exception as exc:
        raise PhysicalDrainError(
            "typed machine command terminal is unresolved"
        ) from exc


def _machine_command_exec_ids(value, path, state_parent, client):
    """Classify journal authority separately from physical exec inspection."""
    operations = value.get("operations")
    if not isinstance(operations, dict):
        raise PhysicalDrainError("physical command journal is unknown")
    quiesced = None
    commands = []
    for operation_id, record in operations.items():
        terminal = (
            isinstance(record, dict)
            and record.get("kind")
            in {"request", "command", "adaptation_recovery", "adaptation_cleanup"}
            and record.get("state") in {"completed", "failed", "timed_out"}
        )
        if not terminal:
            if quiesced is None:
                try:
                    quiesced = machine_quiescence(
                        state_parent, client, path.parent.name
                    )
                except Exception as exc:
                    raise PhysicalDrainError(
                        "physical command outcome is unresolved"
                    ) from exc
            if quiesced.get(operation_id) != _digest(record):
                raise PhysicalDrainError("physical command outcome is unresolved")
            # Attested current retirement is distinct from a command result.
            # No exec ID/exit code is invented and no journal record is changed.
            continue
        if record["kind"] == "command":
            commands.append(_command_terminal(record, operation_id))
    return commands


def _startup_file_identity(info):
    # Reading can update atime; only mutation/identity fields fence this sample.
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _startup_prepare_journals(
    state_parent: Path, workspace_id: str | None = None
) -> dict[str, str]:
    """A pending create is a writer even when no Docker object exists yet."""
    machines = state_parent / "project-machines"
    observed = {}
    producer_reviewed = False
    try:
        if workspace_id is not None and str(UUID(workspace_id)) != workspace_id:
            raise ValueError("startup workspace scope")
        if not machines.exists() and not machines.is_symlink():
            return observed
        if machines.is_symlink() or not machines.is_dir():
            raise ValueError("machine root")
        workspaces = (
            machines.iterdir() if workspace_id is None else (machines / workspace_id,)
        )
        for workspace in workspaces:
            if workspace.is_symlink():
                raise ValueError("workspace path")
            directory = workspace / "startup-prepares"
            if not directory.exists() and not directory.is_symlink():
                continue
            if (
                str(UUID(workspace.name)) != workspace.name
                or directory.is_symlink()
                or not directory.is_dir()
            ):
                raise ValueError("startup directory")
            if not producer_reviewed:
                _portable_quiescence_sources()
                producer_reviewed = True
            for path in directory.iterdir():
                before = path.lstat()
                if (
                    path.suffix != ".json"
                    or not stat.S_ISREG(before.st_mode)
                    or before.st_nlink != 1
                    or before.st_size > 32768
                ):
                    raise ValueError("startup file")
                flags = (
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0)
                )
                with os.fdopen(os.open(path, flags), "rb") as source:
                    opened = os.fstat(source.fileno())
                    if _startup_file_identity(opened) != _startup_file_identity(before):
                        raise ValueError("startup file changed")
                    raw = source.read(32769)
                if len(raw) > 32768 or _startup_file_identity(
                    path.lstat()
                ) != _startup_file_identity(before):
                    raise ValueError("startup file changed")
                from yleum_orchestrator.services.machine_startup import PrepareRecord

                record = PrepareRecord.model_validate_json(raw, strict=True)
                if (
                    record.state not in {"completed", "recovered"}
                    or str(record.binding.workspace_id) != workspace.name
                    or str(record.binding.operation_id) != path.stem
                    or record.model_dump(mode="json") != json.loads(raw)
                ):
                    raise ValueError("startup outcome or identity")
                observed[str(path.relative_to(state_parent))] = hashlib.sha256(
                    raw
                ).hexdigest()
    except Exception as exc:
        raise PhysicalDrainError("physical startup preparation is unresolved") from exc
    return observed


def require_physical_writers_drained(
    state_parent: Path, client: object
) -> dict[str, int]:
    """Read actual helper/exec state in addition to historical terminal SQL proof."""
    containers = client.containers.list(all=True)
    startup_prepares = _startup_prepare_journals(state_parent)
    by_name = {}
    by_id = {}
    inspected = {}

    def inspect(exec_id: str):
        if exec_id not in inspected:
            try:
                inspected[exec_id] = client.api.exec_inspect(exec_id)
            except Exception as exc:
                from docker.errors import NotFound

                if type(exc) is NotFound and exc.status_code == 404:
                    inspected[exec_id] = None
                else:
                    raise PhysicalDrainError(
                        "physical exec inspection is unknown"
                    ) from exc
        return inspected[exec_id]

    for container in containers:
        container.reload()
        attrs = container.attrs
        labels = attrs.get("Config", {}).get("Labels") or {}
        kind = labels.get("omnia.resource_kind")
        if kind == "publication-migration-verifier" or container.name.startswith(
            "omnia-publication-migration-"
        ):
            # Even an exited verifier is unresolved until its exact-owned cleanup
            # is confirmed. Never restart the controller around an orphan helper.
            raise PhysicalDrainError(
                "publication migration verifier cleanup is not confirmed"
            )
        affected = (
            labels.get("omnia.helper") == "true"
            or labels.get("omnia.project_machine") == "true"
            or "omnia.source_operation_id" in labels
            # The trusted core bootstrap inherits project-cell identity labels,
            # unlike helpers created by DockerMachineBackend.
            or labels.get("omnia.project_cell") == "true"
            and kind == "core-database-bootstrap"
        )
        if not affected:
            continue
        for key in ("workspace_id", "project_id", "owner_id"):
            value = labels.get("omnia." + key)
            if not isinstance(value, str) or str(UUID(value)) != value:
                raise PhysicalDrainError("affected physical writer identity is unknown")
        by_name[container.name] = (attrs, labels)
        by_id[container.id] = (attrs, labels)
        helper = (
            labels.get("omnia.helper") == "true"
            or kind in _MACHINE_HELPERS
            or "omnia.source_operation_id" in labels
        )
        if not helper and kind not in _MACHINE_SERVICES:
            raise PhysicalDrainError("affected physical resource kind is unknown")
        state = attrs.get("State", {})
        if helper and not (
            state.get("Status") == "exited"
            and state.get("Running") is False
            and state.get("Paused") is False
            and state.get("Dead") is False
            and type(state.get("Pid")) is int
            and state["Pid"] == 0
            and attrs.get("HostConfig", {}).get("RestartPolicy", {}).get("Name") == "no"
        ):
            raise PhysicalDrainError("affected physical helper is active or unknown")
        exec_ids = attrs.get("ExecIDs")
        if exec_ids is not None and not isinstance(exec_ids, list):
            raise PhysicalDrainError("affected physical exec inventory is unknown")
        services, _commands = _machine_execs(state_parent, labels)
        for exec_id in set(exec_ids or []):
            if not isinstance(exec_id, str) or not re.fullmatch(
                r"[0-9a-f]{64}", exec_id
            ):
                raise PhysicalDrainError("affected physical exec identity is unknown")
            execution = inspect(exec_id)
            if (
                execution is None
                or execution.get("ContainerID") != attrs.get("Id")
                or not (
                    execution.get("Running") is False
                    and type(execution.get("ExitCode")) is int
                    or exec_id in services
                    and execution.get("Running") is True
                    and execution.get("ExitCode") is None
                )
            ):
                raise PhysicalDrainError("affected physical exec is active or unknown")

    journals = 0
    missing_exec_workspaces = set()
    missing_exec_ids = set()
    for family, filename in (
        ("project-machines", "machine.json"),
        ("source-write-operations", "*.json"),
        ("command-operations", "*.json"),
    ):
        root = state_parent / family
        if root.is_symlink():
            raise PhysicalDrainError("physical journal root is unsafe")
        for path in root.glob("*/" + filename):
            if path.is_symlink() or path.parent.is_symlink():
                raise PhysicalDrainError("physical journal path is unsafe")
            value = json.loads(path.read_bytes())
            identity = (
                value.get("binding", {}) if family == "command-operations" else value
            )
            if (
                str(UUID(path.parent.name)) != path.parent.name
                or identity.get("workspace_id") != path.parent.name
            ):
                raise PhysicalDrainError("physical journal identity is unknown")
            if family == "command-operations":
                _terminal_record(value, family, path)
            elif family == "source-write-operations":
                _terminal_record(value, family, path)
                helpers = value.get("helpers")
                if not isinstance(helpers, dict):
                    raise PhysicalDrainError("source helper journal is unknown")
                for name, expected in helpers.items():
                    if name in by_name:
                        _attrs, actual = by_name[name]
                        if not isinstance(expected, dict) or any(
                            actual.get(k) != v for k, v in expected.items()
                        ):
                            raise PhysicalDrainError(
                                "source helper receipt binding changed"
                            )
            else:
                for exec_id in _machine_command_exec_ids(
                    value, path, state_parent, client
                ):
                    execution = inspect(exec_id)
                    if execution is None:
                        missing_exec_workspaces.add(path.parent.name)
                        missing_exec_ids.add(exec_id)
                    else:
                        owner = by_id.get(execution.get("ContainerID"))
                        if (
                            owner is None
                            or owner[1]["omnia.workspace_id"] != path.parent.name
                            or owner[1].get("omnia.resource_kind") != "development"
                            or execution.get("Running") is not False
                            or type(execution.get("ExitCode")) is not int
                        ):
                            raise PhysicalDrainError(
                                "command physical owner or outcome is unknown"
                            )
            journals += 1
    # One complete repeated sample per workspace, not per historical command.
    for workspace_id in sorted(missing_exec_workspaces):
        development = [
            container
            for container in containers
            if container.id in by_id
            and by_id[container.id][1]["omnia.workspace_id"] == workspace_id
            and by_id[container.id][1].get("omnia.resource_kind") == "development"
        ]
        if development:
            _require_current_command_absence(state_parent, client, development)
    for exec_id in missing_exec_ids:
        inspected.pop(exec_id, None)
        if inspect(exec_id) is not None:
            raise PhysicalDrainError("missing command inspection changed")
    # A helper appearing after enumeration invalidates the sample; there is no
    # expiry-as-termination inference and no mutation of foreign resources.
    if {c.id for c in client.containers.list(all=True)} != {c.id for c in containers}:
        raise PhysicalDrainError(
            "physical container inventory changed during inspection"
        )
    if _startup_prepare_journals(state_parent) != startup_prepares:
        raise PhysicalDrainError(
            "physical startup preparation changed during inspection"
        )
    return {"affected_containers": len(by_name), "physical_journals": journals}


def require_migrations_drained(root: Path) -> None:
    """Durable exclusion also covers orphan journals and lost asyncio tasks."""
    active = {"preparing", "applying", "reconciling"}
    states = active | {"prepared", "applied", "failed", "stale", "blocked"}
    for path in root.glob("*/migrations/*.json"):
        try:
            if any(item.is_symlink() for item in (path, path.parent, path.parent.parent)):
                raise ValueError("unsafe migration journal")
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("oversized migration journal")
            value = json.loads(path.read_text(encoding="utf-8"))
            if (
                value["version"] != 1
                or value["project_id"] != path.parent.parent.name
                or str(UUID(value["project_id"])) != value["project_id"]
                or value["operation_id"] != path.stem
                or str(UUID(value["operation_id"])) != path.stem
                or value["state"] not in states
                or type(value["effect_possible"]) is not bool
                or value["effect_possible"]
                or value["state"] in active
            ):
                raise ValueError("unfinished or unknown migration")
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise RuntimeError("migration journals are not quiescent") from exc


def _require_configuration_writes_drained(root: Path) -> None:
    locks = root / "locks"
    try:
        if locks.is_symlink():
            raise ValueError("unsafe publication lock directory")
        for path in locks.glob("configuration-*.lock"):
            import fcntl

            project_id = path.stem.removeprefix("configuration-")
            if str(UUID(project_id)) != project_id:
                raise ValueError("invalid publication configuration lock")
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != root.stat().st_uid or stat.S_IMODE(info.st_mode) != 0o600):
                    raise ValueError("unsafe publication configuration lock")
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
    except (OSError, ValueError) as exc:
        raise RuntimeError("publication configuration writes are not quiescent") from exc


def require_publications_drained(root: Path) -> int:
    if not root.parent.is_dir() or root.is_symlink():
        raise RuntimeError("publication storage authority is unavailable")
    _require_configuration_writes_drained(root)
    require_migrations_drained(root)
    count = 0
    for path in root.glob("*/publication.json"):
        try:
            if path.is_symlink() or path.parent.is_symlink():
                raise ValueError("unsafe journal path")
            value = json.loads(path.read_text(encoding="utf-8"))
            if value["project_id"] != path.parent.name:
                raise ValueError("journal identity mismatch")
            history = value["history"]
            if not isinstance(history, list):
                raise ValueError("invalid journal history")
            if history and history[-1]["response"]["phase"] not in {"done", "failed"}:
                raise ValueError("unfinished or unknown publication")
            count += 1
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            # No log bodies/project data are emitted, including malformed JSON.
            raise RuntimeError("publication journals are not quiescent") from exc
    return count


def live_publication_root(*, expected_cwd: Path | None = None) -> Path:
    processes = set()
    for service in ("yleum-orchestrator", "omnia-orchestrator"):
        result = subprocess.run(
            ["systemctl", "show", service, "--property=MainPID", "--value"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip().isdigit():
            pid = int(result.stdout.strip())
            if pid > 0:
                processes.add(pid)
    if len(processes) != 1:
        raise RuntimeError("expected exactly one live controller process")
    proc = _PROC_ROOT / str(processes.pop())
    if b"yleum_orchestrator" not in (proc / "cmdline").read_bytes():
        raise RuntimeError("controller process identity is unconfirmed")
    cwd = (proc / "cwd").resolve(strict=True)
    if expected_cwd is not None and cwd != expected_cwd.resolve(strict=True):
        raise RuntimeError("controller checkout identity is unconfirmed")
    environment = dict(
        item.decode().split("=", 1)
        for item in (proc / "environ").read_bytes().split(b"\0")
        if b"=" in item
    )
    # Changes affect this read-only helper only, never the live process. Settings
    # resolve the same environment/.env defaults as that controller; no secrets print.
    os.environ.clear()
    os.environ.update(environment)
    os.chdir(cwd)
    sys.path.insert(0, str(Path.cwd() / "src"))
    from yleum_orchestrator.core.config import get_settings

    return Path(get_settings().cell_state_path).parent / "cell-publications"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-machine-quiescence", metavar="WORKSPACE_UUID")
    args = parser.parse_args()
    try:
        root = live_publication_root()
        count = require_publications_drained(root)
        import docker

        from yleum_orchestrator.core.config import get_settings

        client = docker.DockerClient(base_url=get_settings().docker_host, timeout=10)
        try:
            if args.record_machine_quiescence:
                covered = machine_quiescence(
                    root.parent, client, args.record_machine_quiescence, record=True
                )
                print(
                    json.dumps(
                        {"machine_quiescence_recorded": True, "commands": len(covered)}
                    )
                )
            physical = require_physical_writers_drained(root.parent, client)
        finally:
            client.close()
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, PhysicalDrainError)
            else "publication_or_physical_unknown"
        )
        print(json.dumps({"publication_drained": False, "reason": reason}))
        raise SystemExit(75) from None
    print(json.dumps({"publication_drained": True, "journals": count, **physical}))
