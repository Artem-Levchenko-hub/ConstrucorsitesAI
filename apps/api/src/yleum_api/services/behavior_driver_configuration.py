"""Operator-only driver installation. No model state, import or path fallback."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import fields, replace
from typing import Any

from yleum_api.services.behavior_compilation_resolver import resolve_retained_compilation
from yleum_api.services.max_behavior_browser import (
    CoffeeSummaryAdapter,
    DensityAdapter,
    DensityView,
    PlatformBrowserAdapter,
    ThemeAdapter,
    make_private_browser_driver,
)
from yleum_api.services.max_behavior_proof import (
    BehaviorDriverInput,
    BehaviorProofError,
    BrowserBehaviorObservation,
    CompiledAssetWitness,
    ControllerBehaviorDriver,
    ObservedAsset,
)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _json(raw: str) -> Any:
    if len(raw) > 32768:
        raise ValueError
    return json.loads(raw, object_pairs_hook=_pairs)


def _pin(path: str, expected: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or not path.startswith("/"):
        raise ValueError
    parts = path[1:].split("/")
    if not all(p and p not in {".", ".."} for p in parts):
        raise ValueError
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    fd = None
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or not before.st_mode & 0o111
            or not 0 < before.st_size <= 384 * 1024**2
        ):
            raise ValueError
        sha = hashlib.sha256()
        size = 0
        while chunk := os.read(fd, 65536):
            size += len(chunk)
            if size > 384 * 1024**2:
                raise ValueError
            sha.update(chunk)
        after = os.fstat(fd)

        def stamp(s: os.stat_result) -> tuple[int, int, int, int, int]:
            return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns

        if stamp(before) != stamp(after) or sha.hexdigest() != expected:
            raise ValueError
    finally:
        os.close(directory)
        if fd is not None:
            os.close(fd)


def _adapter(raw: Any) -> PlatformBrowserAdapter:
    if type(raw) is dict and "preset" in raw:
        from yleum_api.services.max_behavior_ui_contract import COFFEE_SELECTORS, COFFEE_UI_PRESET

        if set(raw) - {"preset", "read_paths"} or raw["preset"] != COFFEE_UI_PRESET:
            raise ValueError
        raw = {"coffee_summary": dict(COFFEE_SELECTORS), "read_paths": raw.get("read_paths", [])}
    if type(raw) is not dict or set(raw) - {"density", "theme", "coffee_summary", "read_paths"}:
        raise ValueError
    parsed = dict(raw)
    for name, cls in [
        ("density", DensityAdapter),
        ("theme", ThemeAdapter),
        ("coffee_summary", CoffeeSummaryAdapter),
    ]:
        value = parsed.get(name)
        if value is None:
            continue
        if type(value) is not dict or set(value) - {f.name for f in fields(cls)}:
            raise ValueError
        value = dict(value)
        if name == "density":
            for view in ["list_view", "board_view"]:
                value[view] = DensityView(**value[view])
        for key, item in value.items():
            if key in {"list_view", "board_view"}:
                if not all(type(v) is str and 0 < len(v) <= 256 for v in vars_for_view(item)):
                    raise ValueError
            elif item is not None and (type(item) is not str or not 0 < len(item) <= 256):
                raise ValueError
        parsed[name] = cls(**value)
    reads = parsed.get("read_paths", [])
    if (
        type(reads) is not list
        or len(reads) > 32
        or not all(
            type(p) is str and re.fullmatch(r"/api/[A-Za-z0-9_/-]{1,160}", p) and ".." not in p
            for p in reads
        )
    ):
        raise ValueError
    parsed["read_paths"] = tuple(reads)
    result = PlatformBrowserAdapter(**parsed)
    if not any([result.density, result.theme, result.coffee_summary]):
        raise ValueError
    return result


def vars_for_view(value: DensityView) -> tuple[str, ...]:
    return tuple(getattr(value, f.name) for f in fields(value))


def configured_behavior_driver(handle: Any, settings: Any) -> ControllerBehaviorDriver | None:
    if not settings.max_behavior_browser_executable:
        return None
    try:
        path, pin = settings.max_behavior_browser_executable, settings.max_behavior_browser_sha256
        if getattr(settings, "env", "dev") == "prod":
            from yleum_api.services.behavior_browser_installation import validate_installation

            validate_installation(path, pin)
        _pin(path, pin)
        raw = _json(settings.max_behavior_adapter_registry)
        if type(raw) is not dict or not raw or set(raw) - {"max-miniapp-nextjs"}:
            raise ValueError
        adapters = {key: _adapter(value) for key, value in raw.items()}
        vendors = (
            _json(settings.max_behavior_vendor_asset_registry)
            if settings.max_behavior_vendor_asset_registry
            else []
        )
        if type(vendors) is not list or len(vendors) > 1:
            raise ValueError
        platform_assets = []
        for item in vendors:
            if (
                type(item) is not dict
                or set(item) != {"url", "sha256", "bytes"}
                or item["url"] != "https://st.max.ru/js/max-web-app.js"
            ):
                raise ValueError
            if (
                not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                or type(item["bytes"]) is not int
                or not 0 < item["bytes"] <= 8388608
            ):
                raise ValueError
            platform_assets.append(ObservedAsset(item["url"], item["sha256"], item["bytes"]))

        async def resolver(request: BehaviorDriverInput) -> CompiledAssetWitness:
            witness = await resolve_retained_compilation(handle, request)
            return replace(witness, platform_assets=tuple(platform_assets))

        drivers = {
            key: make_private_browser_driver(
                executable_path=path, adapter=adapter, resolve_candidate_compilation=resolver
            )
            for key, adapter in adapters.items()
        }

        def selected(request: BehaviorDriverInput) -> ControllerBehaviorDriver:
            try:
                if getattr(settings, "env", "dev") == "prod":
                    validate_installation(path, pin)
                _pin(path, pin)  # recheck before every private execution, never model-owned
            except Exception:
                raise BehaviorProofError("BEHAVIOR_CONFIGURATION_UNAVAILABLE") from None
            template = (
                "max-miniapp-nextjs"
                if request.contract.template == "max_miniapp"
                else request.contract.template
            )
            if template not in drivers:
                raise BehaviorProofError("BEHAVIOR_ADAPTER_UNSUPPORTED")
            return drivers[template]

        async def compiled(request: BehaviorDriverInput) -> CompiledAssetWitness:
            return await selected(request).collect_compiled(request)

        async def observed(
            request: BehaviorDriverInput, witness: CompiledAssetWitness
        ) -> BrowserBehaviorObservation:
            return await selected(request).observe_browser(request, witness)

        capabilities = frozenset().union(
            *(driver.supported_capabilities for driver in drivers.values())
        )
        return ControllerBehaviorDriver(compiled, observed, capabilities)
    except Exception:
        raise BehaviorProofError("BEHAVIOR_CONFIGURATION_UNAVAILABLE") from None
