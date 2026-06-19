"""RQ job: read committed files → build_spec → orchestrator /build-exe → upload to MinIO
→ publish exe.* SSE.

Mirrors workers/preview.py in structure. Self-heal (exe_doctor) retries up to MAX_HEAL
times when PyInstaller/NSIS fails with a patchable error.

Flow:
  1. build_spec(files)  — pure, derives entry/windowed/datas from the source tree
  2. render(spec)       — emits build_spec.json + installer.nsi strings
  3. orchestrator.build_exe() — ships everything to /build-exe, waits up to 360s
  4. if ok: put_exe_artifacts → publish exe.ready
     if err + attempts remain: exe_doctor.heal → patch spec → retry
     if err + cap hit or heal gives up: publish exe.failed

SSE channel: the project's standard `omnia:project:<project_id>` Redis channel.
Events:
  exe.stage  {build_id, stage}             — "build" before each orchestrator call
  exe.heal   {build_id, attempt}           — emitted before each self-heal retry
  exe.ready  {build_id, setup_url, exe_url, name, size}
  exe.failed {build_id, log}               — last 4 000 chars of the build log
"""

from __future__ import annotations

import asyncio
import base64
import structlog

from omnia_api.core.config import get_settings
from omnia_api.core.minio import put_exe_artifacts
from omnia_api.core.redis import publish_event
from omnia_api.services import exe_build, exe_doctor, orchestrator_client

log = structlog.get_logger(__name__)

MAX_HEAL = 3  # maximum self-heal retries after first failure


def build_exe_job(
    project_id: str,
    build_id: str,
    slug: str,
    files: dict[str, str],
) -> None:
    """RQ entry point — synchronous wrapper around the async implementation."""
    asyncio.run(_run(project_id, build_id, slug, files))


async def _run(
    project_id: str,
    build_id: str,
    slug: str,
    files: dict[str, str],
) -> None:
    settings = get_settings()

    spec = exe_build.build_spec(files, slug=slug)

    log.info(
        "exe_build.start",
        project_id=project_id,
        build_id=build_id,
        entry=spec.entry,
        name=spec.name,
    )

    # Retry loop: up to MAX_HEAL + 1 attempts (1 initial + MAX_HEAL self-healed).
    res: dict = {}
    for attempt in range(MAX_HEAL + 1):
        rendered = exe_build.render(spec)

        # Announce "build started" (or re-started after a heal) to the UI.
        try:
            await publish_event(
                project_id, "exe.stage", {"build_id": build_id, "stage": "build"}
            )
        except Exception as exc:  # noqa: BLE001 — fail-soft, never abort the build (R-10)
            log.warning("exe_build.stage_publish_failed", err=str(exc))

        # --- call orchestrator ------------------------------------------------
        try:
            res = await orchestrator_client.build_exe(
                name=spec.name,
                files=files,
                pyinstaller_args=exe_build.render_pyinstaller_args(spec),
                installer_nsi=rendered["installer.nsi"],
                requirements=spec.requirements,
            )
        except Exception as exc:  # noqa: BLE001 — network / 5xx from orchestrator
            log.error("exe_build.orchestrator_error", attempt=attempt, err=str(exc))
            res = {"ok": False, "log": str(exc)}

        if res.get("ok"):
            # Success path — break out and upload.
            break

        # Build failed.
        raw_log: str = res.get("log") or ""
        log.warning(
            "exe_build.orchestrator_not_ok",
            build_id=build_id,
            attempt=attempt,
            log_tail=raw_log[-200:],
        )

        remaining = MAX_HEAL - attempt
        if remaining <= 0:
            # Cap hit — give up.
            break

        # Ask exe_doctor for a patch before the next attempt.
        try:
            await publish_event(
                project_id, "exe.heal", {"build_id": build_id, "attempt": attempt}
            )
        except Exception as exc:  # noqa: BLE001 — fail-soft (R-10)
            log.warning("exe_build.heal_publish_failed", err=str(exc))

        patched = await exe_doctor.heal(raw_log, spec, files)
        if patched is None:
            log.info("exe_build.heal_gave_up", build_id=build_id, attempt=attempt)
            break
        log.info(
            "exe_build.heal_applied",
            build_id=build_id,
            attempt=attempt,
            new_hi=patched.hidden_imports,
            new_ca=patched.collect_all,
        )
        spec = patched
        # Continue to next loop iteration with the patched spec.

    else:
        # Loop exhausted without a `break` — should not happen with current logic,
        # but guard it so we always publish exe.failed in this branch too.
        pass

    if not res.get("ok"):
        await _publish_failed(project_id, build_id, res.get("log") or "")
        return

    # --- decode & size-check --------------------------------------------------
    try:
        setup_bytes = base64.b64decode(res["setup_b64"])
    except Exception as exc:  # noqa: BLE001
        log.error("exe_build.decode_error", err=str(exc))
        await _publish_failed(project_id, build_id, f"base64 decode error: {exc}")
        return

    exe_bytes: bytes = b""
    if res.get("exe_b64"):
        try:
            exe_bytes = base64.b64decode(res["exe_b64"])
        except Exception as exc:  # noqa: BLE001
            log.warning("exe_build.exe_decode_error", err=str(exc))
            # Non-fatal: Setup.exe is the primary deliverable; bare .exe is optional.

    max_bytes = settings.exe_build_max_mb * 1024 * 1024
    if len(setup_bytes) > max_bytes:
        msg = (
            f"Setup.exe too large: {len(setup_bytes) / 1024 / 1024:.1f} MB "
            f"(limit {settings.exe_build_max_mb} MB)"
        )
        log.error("exe_build.too_large", build_id=build_id, size=len(setup_bytes))
        await _publish_failed(project_id, build_id, msg)
        return

    # --- upload to MinIO ------------------------------------------------------
    try:
        urls = put_exe_artifacts(project_id, build_id, spec.name, setup_bytes, exe_bytes)
    except Exception as exc:  # noqa: BLE001
        log.error("exe_build.minio_error", err=str(exc))
        await _publish_failed(project_id, build_id, f"MinIO upload failed: {exc}")
        return

    log.info(
        "exe_build.ready",
        project_id=project_id,
        build_id=build_id,
        setup_url=urls["setup_url"],
        size=urls["size"],
    )

    # --- publish success -------------------------------------------------------
    try:
        await publish_event(
            project_id,
            "exe.ready",
            {
                "build_id": build_id,
                "setup_url": urls["setup_url"],
                "exe_url": urls["exe_url"],
                "name": urls["name"],
                "size": urls["size"],
            },
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft (R-10)
        log.warning("exe_build.ready_publish_failed", err=str(exc))


async def _publish_failed(project_id: str, build_id: str, raw_log: str) -> None:
    """Best-effort publish of exe.failed; swallows its own errors (R-10)."""
    try:
        await publish_event(
            project_id,
            "exe.failed",
            {"build_id": build_id, "log": raw_log[-4000:]},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("exe_build.failed_publish_failed", err=str(exc))
