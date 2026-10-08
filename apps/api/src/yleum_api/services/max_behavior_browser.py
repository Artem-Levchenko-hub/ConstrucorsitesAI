"""Private, GET-only Playwright witness for a trusted candidate compilation.

Adapters and the compilation resolver are platform inputs. They must never be
constructed from generated application metadata, model output, or agent_state.
There is no default adapter or source-digest fallback.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from yleum_api.services import max_behavior_browser_core as core
from yleum_api.services.max_behavior_proof import (
    PROBE_SHA256,
    PROBE_VERSION,
    BehaviorDriverInput,
    BehaviorProofError,
    BrowserBehaviorObservation,
    CompiledAssetWitness,
    ControllerBehaviorDriver,
    _compiled,
    digest,
    need,
)
from yleum_api.services.orchestrator_client import ProjectCellPreviewSession

_API_SOURCE_ROOT = str(Path(__file__).resolve().parents[2])


class _PrivatePipe:
    """Owned nonblocking POSIX pipe. No asynchronous transport can appear later."""

    def __init__(self, stream: Any) -> None:
        self.stream = stream
        self.fd = stream.fileno()
        os.set_blocking(self.fd, False)
        self.buffer = bytearray()
        self.closed = False
        self.registrations: dict[
            bool, tuple[asyncio.AbstractEventLoop, asyncio.Future[None], object]
        ] = {}

    async def ready(self, *, writing: bool = False) -> None:
        need(not self.is_closing(), "BEHAVIOR_DRIVER_FAILED")
        loop = asyncio.get_running_loop()
        event: asyncio.Future[None] = loop.create_future()
        token = object()

        def wake() -> None:
            if not event.done():
                event.set_result(None)

        register = loop.add_writer if writing else loop.add_reader
        remove = loop.remove_writer if writing else loop.remove_reader
        previous = self.registrations.pop(writing, None)
        if previous is not None:
            old_loop, old_event, _old_token = previous
            old_remove = old_loop.remove_writer if writing else old_loop.remove_reader
            old_remove(self.fd)
            old_event.cancel()
        self.registrations[writing] = (loop, event, token)
        register(self.fd, wake)
        try:
            await event
        finally:
            current = self.registrations.get(writing)
            if current is not None and current[2] is token:
                del self.registrations[writing]
                if not self.closed:
                    remove(self.fd)

    async def chunk(self) -> bytes:
        while True:
            try:
                return os.read(self.fd, 4096)
            except BlockingIOError:
                await self.ready()

    async def readline(self) -> bytes:
        while b"\n" not in self.buffer:
            piece = await self.chunk()
            if not piece:
                break
            self.buffer.extend(piece)
            need(len(self.buffer) <= 131072, "BEHAVIOR_DRIVER_FAILED")
        newline = self.buffer.find(b"\n")
        count = newline + 1 if newline >= 0 else len(self.buffer)
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result

    async def read(self, limit: int) -> bytes:
        data = bytearray(self.buffer)
        self.buffer.clear()
        while len(data) < limit:
            piece = await self.chunk()
            if not piece:
                break
            data.extend(piece)
        return bytes(data)

    async def write(self, payload: bytes) -> None:
        position = 0
        while position < len(payload):
            try:
                position += os.write(self.fd, payload[position:])
            except BlockingIOError:
                await self.ready(writing=True)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        pending = self.registrations
        self.registrations = {}
        # Unregister while the descriptor is still owned, then close it. A
        # canceled old finally can never remove a recycled descriptor's reader.
        for writing, (loop, event, _token) in pending.items():
            remove = loop.remove_writer if writing else loop.remove_reader
            remove(self.fd)
            event.cancel()
        self.stream.close()

    def is_closing(self) -> bool:
        return self.closed or bool(self.stream.closed)


class _OwnedProcess:
    """Acquire the platform guardian PID synchronously before the first await."""

    def __init__(self, environment: dict[str, str]) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-m", "yleum_api.services.max_behavior_browser_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            start_new_session=True,
            env=environment,
        )
        try:
            self.stdin = _PrivatePipe(self.process.stdin)
            self.stdout = _PrivatePipe(self.process.stdout)
            self.exited = _PrivatePipe(
                os.fdopen(os.pidfd_open(self.process.pid), "rb", buffering=0)
            )
        except BaseException:
            self.process.kill()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            raise BehaviorProofError("BEHAVIOR_BROWSER_CLEANUP_FAILED") from None
        self.pid = self.process.pid

    @property
    def returncode(self) -> int | None:
        return self.process.poll()

    async def wait(self) -> int:
        if self.returncode is None:
            await self.exited.ready()
        code = self.returncode
        need(code is not None, "BEHAVIOR_BROWSER_CLEANUP_FAILED")
        return cast(int, code)

    def send_signal(self, value: signal.Signals) -> None:
        self.process.send_signal(value)

    def close_pipes(self) -> None:
        self.stdin.close()
        self.stdout.close()
        self.exited.close()


@dataclass(frozen=True, slots=True)
class DensityView:
    control: str
    container: str
    cards: str


@dataclass(frozen=True, slots=True)
class DensityAdapter:
    normal: str
    compact: str
    storage_key: str
    filter: str
    filter_value: str
    controls: str
    list_view: DensityView
    board_view: DensityView
    normal_name: str = "Normal"
    compact_name: str = "Compact"

    def config(self) -> dict[str, Any]:
        from dataclasses import asdict

        result = asdict(self)
        result["names"] = {
            "normal": result.pop("normal_name"),
            "compact": result.pop("compact_name"),
        }
        result["views"] = [result.pop("list_view"), result.pop("board_view")]
        return result


@dataclass(frozen=True, slots=True)
class ThemeAdapter:
    light: str
    dark: str
    header: str
    card: str
    text: str
    storage_key: str
    view_control: str | None = None
    light_name: str = "Light"
    dark_name: str = "Dark"

    def config(self) -> dict[str, Any]:
        from dataclasses import asdict

        result = asdict(self)
        result["names"] = {"light": result.pop("light_name"), "dark": result.pop("dark_name")}
        return result


@dataclass(frozen=True, slots=True)
class CoffeeSummaryAdapter:
    button: str
    form: str
    text_input: str
    summary: str


@dataclass(frozen=True, slots=True)
class PlatformBrowserAdapter:
    density: DensityAdapter | None = None
    theme: ThemeAdapter | None = None
    coffee_summary: CoffeeSummaryAdapter | None = None
    # Exact approved read endpoints; no query strings, wildcard or write paths.
    read_paths: tuple[str, ...] = ()


def executed_probe_digest() -> str:
    return digest(
        {
            "driver": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "measurements": hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest(),
            "guardian": hashlib.sha256(
                Path(__file__).with_name("max_behavior_browser_worker.py").read_bytes()
            ).hexdigest(),
        }
    )


def _origin(request: BehaviorDriverInput) -> tuple[str, str]:
    session = request.preview.session
    need(
        type(session) is ProjectCellPreviewSession
        and session.workspace_id == request.binding.workspace_id,
        "BEHAVIOR_PREVIEW_UNAVAILABLE",
    )
    # Revalidate against the configured platform host registry, not app data.
    session.__post_init__()
    return session.preview_url.rstrip("/"), session.bootstrap_url


def _authenticate(context: Any, bootstrap: str) -> None:
    response = context.request.get(bootstrap, timeout=5000, max_redirects=0)
    need(response.status in {200, 302, 303, 307}, "BEHAVIOR_PREVIEW_UNAVAILABLE")
    if response.status != 200:
        target = response.headers.get("location", "")
        need(target == "/", "BEHAVIOR_PREVIEW_UNAVAILABLE")


def _paint_controls(page: Any, selectors: tuple[str, ...]) -> list[dict[str, Any]]:
    fields = (
        "tag",
        "effective_opacity",
        "css_visible",
        "display",
        "visibility",
        "unsupported_paint",
        "box",
        "in_view",
        "hit",
    )
    return [
        {key: witness[key] for key in fields}
        for selector in selectors
        for witness in [core.painted(page, page.locator(selector))]
    ]


def _mode(measured: dict[str, Any], filter_value: str) -> dict[str, Any]:
    return {
        "data_sha256": measured["data_sha256"],
        "filter_sha256": digest(filter_value),
        "count": measured["count"],
        "padding": min(x["padding"] for x in measured["cards"]),
        "height": min(x["height"] for x in measured["cards"]),
        "gap": measured["gap"],
    }


def _measure(
    page: Any,
    request: BehaviorDriverInput,
    adapter: PlatformBrowserAdapter,
    viewport: dict[str, int],
) -> dict[str, list[dict[str, Any]]]:
    measurements = {}
    if "task_density_v1" in request.contract.capabilities:
        need(adapter.density is not None, "BEHAVIOR_ADAPTER_UNSUPPORTED")
        cfg = cast(DensityAdapter, adapter.density).config()
        observed = core.check_density(page, cfg)
        measurements["task_density_v1"] = [
            {
                "viewport": viewport,
                "view": view,
                **{
                    mode: _mode(row[mode], cfg["filter_value"])
                    for mode in ("normal", "compact", "reload")
                },
                "controls_min_width": 44,
                "controls_min_height": 44,
                "local_preference": "compact",
                "reload_preference": "compact",
                "paint": _paint_controls(page, (cfg["normal"], cfg["compact"])),
            }
            for view, row in zip(("list", "board"), observed, strict=True)
        ]
    if "header_theme_v1" in request.contract.capabilities:
        need(adapter.theme is not None, "BEHAVIOR_ADAPTER_UNSUPPORTED")
        cfg = cast(ThemeAdapter, adapter.theme).config()
        theme_observed = core.check_theme(page, cfg)
        measurements["header_theme_v1"] = [
            {
                "viewport": viewport,
                **{
                    mode: {
                        key + "_luminance": core.luminance(color)
                        for key, color in theme_observed[mode].items()
                    }
                    for mode in ("light", "dark", "reload")
                },
                "header_control_visible": True,
                "controls_min_width": 44,
                "controls_min_height": 44,
                "local_preference": "dark",
                "reload_preference": "dark",
                "paint": _paint_controls(page, (cfg["light"], cfg["dark"])),
            }
        ]
    if "coffee_local_summary_v1" in request.contract.capabilities:
        from dataclasses import asdict

        need(adapter.coffee_summary is not None, "BEHAVIOR_ADAPTER_UNSUPPORTED")
        cfg = asdict(cast(CoffeeSummaryAdapter, adapter.coffee_summary))
        observed = core.check_coffee_summary(page, cfg)
        measurements["coffee_local_summary_v1"] = [
            {
                "viewport": viewport,
                "rounds": observed,
                "controls_min_width": 44,
                "controls_min_height": 44,
                "paint": _paint_controls(page, (cfg["button"], cfg["button"])),
            }
        ]
    return measurements


async def _execute_owned_process(
    request: BehaviorDriverInput,
    witness: CompiledAssetWitness,
    observe: bool,
    *,
    executable_path: str,
    launch_args: tuple[str, ...],
    adapter: PlatformBrowserAdapter,
    timeout_seconds: float,
    on_closed: Callable[[int, int, bool], None] | None,
) -> CompiledAssetWitness | BrowserBehaviorObservation:
    from dataclasses import asdict

    origin, bootstrap = _origin(request)
    need(2 <= timeout_seconds <= 35, "BEHAVIOR_ADAPTER_UNSUPPORTED")
    payload = json.dumps(
        {
            "binding": request.binding.to_json(),
            "contract": request.contract.to_json(),
            "assets": [asdict(x) for x in witness.assets],
            "platform_assets": [asdict(x) for x in witness.platform_assets],
            "compilation_receipt_sha256": witness.compilation_receipt_sha256,
            "adapter": asdict(adapter),
            "origin": origin,
            "bootstrap": bootstrap,
            "executable_path": executable_path,
            "launch_args": launch_args,
            "observe": observe,
            "timeout_seconds": timeout_seconds,
        },
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    need(len(payload) <= 131072, "BEHAVIOR_ADAPTER_UNSUPPORTED")
    environment = dict(os.environ)
    environment.update(
        {
            "DEBUG": "",
            "PWDEBUG": "0",
            "PYTHONINSPECT": "",
            "PYTHONSTARTUP": "",
            "PYTHONPATH": _API_SOURCE_ROOT,
        }
    )

    async def bounded(awaitable: Awaitable[Any], seconds: float) -> Any:
        task = asyncio.ensure_future(awaitable)
        try:
            completed, _ = await asyncio.wait({task}, timeout=seconds)
            if not completed:
                raise TimeoutError
            return task.result()
        finally:
            if not task.done():
                task.cancel()  # never await a transport that ignores cancellation

    async def finish_uninterruptibly(task: asyncio.Task[Any]) -> Any:
        # Repeated cancellation cannot extend this absolute cleanup deadline.
        deadline = time.monotonic() + 7
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                task.cancel()
                raise BehaviorProofError("BEHAVIOR_BROWSER_CLEANUP_FAILED")
            try:
                completed, _ = await asyncio.wait({task}, timeout=remaining)
                if not completed:
                    task.cancel()
                    raise BehaviorProofError("BEHAVIOR_BROWSER_CLEANUP_FAILED")
                return task.result()
            except asyncio.CancelledError:
                if task.done():
                    return task.result()

    # No await precedes this ownership acquisition; generated code is not run
    # until the private payload is dispatched inside the registered boundary.
    process = _OwnedProcess(environment)
    owned_group = 0
    closed = False
    cleanup_attempted = False

    async def stop_owned() -> bool:
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
        if process.returncode is None:
            process.send_signal(signal.SIGTERM)
        try:
            await bounded(process.wait(), 3)
        except TimeoutError:
            # Only the explicitly announced executor group and this guardian's
            # own session are targeted. Never match shared browser names.
            for group in (owned_group, process.pid):
                if group:
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            try:
                await bounded(process.wait(), 1)
            except TimeoutError:
                return False
        if process.returncode == 0 and process.stdout is not None:
            try:
                remaining = await bounded(process.stdout.read(131073), 1)
            except TimeoutError:
                return False
            if len(remaining) <= 131072:
                try:
                    messages = [json.loads(line) for line in remaining.splitlines()]
                    if messages and "owned_group" in messages[0]:
                        announced = messages.pop(0)
                        if announced.get("guardian") != process.pid:
                            return False
                    return len(messages) == 1 and messages[0].get("cleanup_closed") is True
                except (ValueError, AttributeError):
                    pass
        return False

    async def cleanup_once() -> bool:
        nonlocal cleanup_attempted
        if cleanup_attempted:
            return closed
        cleanup_attempted = True
        try:
            return bool(await finish_uninterruptibly(asyncio.create_task(stop_owned())))
        except BehaviorProofError:
            return False

    try:
        assert process.stdin is not None and process.stdout is not None

        async def ready() -> None:
            async with asyncio.timeout(5):
                acknowledged = json.loads(await process.stdout.readline())
                need(
                    acknowledged == {"guardian": process.pid, "ready": True},
                    "BEHAVIOR_DRIVER_FAILED",
                )

        await bounded(ready(), 5)
        await process.stdin.write(payload)
        process.stdin.close()
        async with asyncio.timeout(timeout_seconds + 5):
            hello = json.loads(await process.stdout.readline())
            need(
                set(hello) == {"guardian", "owned_group"}
                and hello["guardian"] == process.pid
                and type(hello["owned_group"]) is int
                and hello["owned_group"] > 1,
                "BEHAVIOR_DRIVER_FAILED",
            )
            owned_group = hello["owned_group"]
            output = await process.stdout.readline()
            need(len(output) <= 131072, "BEHAVIOR_DRIVER_FAILED")
            result = json.loads(output)
            await process.wait()
        closed = result.get("cleanup_closed") is True and process.returncode == 0
        need(closed, "BEHAVIOR_BROWSER_CLEANUP_FAILED")
        if result.get("status") != "PASS":
            need(
                set(result) == {"status", "code", "proof_status", "cleanup_closed"},
                "BEHAVIOR_DRIVER_FAILED",
            )
            raise BehaviorProofError(result["code"], result["proof_status"])
        need(set(result) == {"status", "measurements", "cleanup_closed"}, "BEHAVIOR_DRIVER_FAILED")
        if not observe:
            need(result["measurements"] is None, "BEHAVIOR_COMPILED_BINDING_INVALID")
            return witness
        return BrowserBehaviorObservation(
            request.binding,
            witness.digest,
            PROBE_VERSION,
            PROBE_SHA256,
            "PASS_OBSERVED",
            result["measurements"],
        )
    except asyncio.CancelledError:
        closed = await cleanup_once()
        if not closed:
            raise BehaviorProofError("BEHAVIOR_BROWSER_CLEANUP_FAILED") from None
        raise
    except TimeoutError:
        closed = await cleanup_once()
        raise BehaviorProofError(
            "BEHAVIOR_BROWSER_DEADLINE" if closed else "BEHAVIOR_BROWSER_CLEANUP_FAILED"
        ) from None
    finally:
        if process.returncode is None:
            closed = await cleanup_once()
        process.close_pipes()
        if on_closed is not None:
            on_closed(process.pid, owned_group, closed)


def _execute_browser(
    request: BehaviorDriverInput,
    witness: CompiledAssetWitness,
    observe: bool,
    *,
    origin: str,
    bootstrap: str,
    adapter: PlatformBrowserAdapter,
    executable_path: str,
    launch_args: tuple[str, ...],
) -> CompiledAssetWitness | BrowserBehaviorObservation:
    from playwright.sync_api import ViewportSize, sync_playwright

    deadline = time.monotonic() + 30
    need(executed_probe_digest() == PROBE_SHA256, "BEHAVIOR_OBSERVATION_BINDING_INVALID")
    _compiled(witness, request.binding)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=executable_path,
            headless=True,
            timeout=10000,
            args=list(launch_args),
        )
        try:
            if not observe:
                context = browser.new_context(service_workers="block")
                try:
                    _authenticate(context, bootstrap)
                    for asset in (*witness.assets, *witness.platform_assets):
                        need(time.monotonic() < deadline, "BEHAVIOR_DRIVER_FAILED")
                        response = context.request.get(
                            (
                                asset.path
                                if asset.path.startswith("https://")
                                else origin + asset.path
                            ),
                            timeout=5000,
                            max_redirects=0,
                        )
                        body = response.body()
                        need(
                            response.status == 200
                            and len(body) == asset.bytes
                            and hashlib.sha256(body).hexdigest() == asset.sha256,
                            "BEHAVIOR_COMPILED_ASSETS_CHANGED",
                        )
                finally:
                    context.close()
                return witness
            measurements: dict[str, list[dict[str, Any]]] = {}
            approved = {asset.path: asset for asset in witness.assets}
            vendors = {
                (origin + asset.path if asset.path.startswith("/") else asset.path): asset
                for asset in witness.platform_assets
            }
            viewports: tuple[ViewportSize, ViewportSize] = (
                {"width": 1280, "height": 900},
                {"width": 390, "height": 844},
            )
            for viewport in viewports:
                context = browser.new_context(viewport=viewport, service_workers="block")
                try:
                    _authenticate(context, bootstrap)
                    seen: set[str] = set()
                    failures: list[str] = []
                    errors: list[str] = []
                    budget = {"requests": 0, "bytes": 0}

                    def route_request(
                        route: Any,
                        _request: Any,
                        budget: dict[str, int] = budget,
                        failures: list[str] = failures,
                        seen: set[str] = seen,
                    ) -> None:
                        incoming = route.request
                        path = urlsplit(incoming.url).path
                        budget["requests"] += 1
                        vendor = vendors.get(incoming.url)
                        if (
                            time.monotonic() >= deadline
                            or incoming.method != "GET"
                            or (incoming.url != origin + path and vendor is None)
                            or budget["requests"] > 160
                            or (
                                path not in {"/", *approved, *adapter.read_paths} and vendor is None
                            )
                        ):
                            failures.append("request")
                            route.abort()
                            return
                        try:
                            response = route.fetch(timeout=5000, max_redirects=0)
                            body = response.body()
                            budget["bytes"] += len(body)
                            need(
                                response.status == 200
                                and len(body) <= 8388608
                                and budget["bytes"] <= 67108864,
                                "BEHAVIOR_OBSERVATION_UNVERIFIED",
                            )
                            if path in approved or vendor is not None:
                                asset = vendor if vendor is not None else approved[path]
                                need(
                                    len(body) == asset.bytes
                                    and hashlib.sha256(body).hexdigest() == asset.sha256,
                                    "BEHAVIOR_COMPILED_ASSETS_CHANGED",
                                )
                                seen.add(path)
                            route.fulfill(response=response, body=body)
                        except Exception:
                            failures.append("response")
                            route.abort()

                    def websocket(socket: Any, failures: list[str] = failures) -> None:
                        failures.append("websocket")
                        socket.close()

                    context.route("**/*", route_request)
                    context.route_web_socket("**/*", websocket)
                    page: Any = context.new_page()
                    page._behavior_deadline = deadline
                    page.set_default_timeout(2000)

                    def page_error(_error: Any, captured: list[str] = errors) -> None:
                        captured.append("page")

                    page.on("pageerror", page_error)
                    page.goto(origin + "/", wait_until="networkidle", timeout=5000)
                    need(
                        not failures
                        and any(path in approved and path.endswith(".js") for path in seen),
                        "BEHAVIOR_OBSERVATION_UNVERIFIED",
                    )
                    try:
                        for cap, rows in _measure(
                            page,
                            request,
                            adapter,
                            {"width": viewport["width"], "height": viewport["height"]},
                        ).items():
                            measurements.setdefault(cap, []).extend(rows)
                    except core.Failure as error:
                        raise BehaviorProofError(
                            "BEHAVIOR_NEEDS_CHANGES"
                            if error.status == "NEEDS_CHANGES"
                            else "BEHAVIOR_PAINT_UNVERIFIED",
                            error.status,
                        ) from None
                    need(not failures and not errors, "BEHAVIOR_OBSERVATION_UNVERIFIED")
                finally:
                    context.close()
            return BrowserBehaviorObservation(
                request.binding,
                witness.digest,
                PROBE_VERSION,
                PROBE_SHA256,
                "PASS_OBSERVED",
                measurements,
            )
        finally:
            browser.close()


def make_private_browser_driver(
    *,
    executable_path: str,
    adapter: PlatformBrowserAdapter,
    launch_args: tuple[str, ...] = (),
    worker_timeout_seconds: float = 35,
    on_worker_closed: Callable[[int, int, bool], None] | None = None,
    resolve_candidate_compilation: Callable[[BehaviorDriverInput], Awaitable[CompiledAssetWitness]],
) -> ControllerBehaviorDriver:
    """Inject a platform resolver of actual compiled bytes, never a model manifest.

    The resolver must verify candidate/source/fence against its compilation
    receipt. This driver checks that those exact bytes are actually served.
    """
    need(type(adapter) is PlatformBrowserAdapter, "BEHAVIOR_ADAPTER_UNSUPPORTED")
    need(
        all(
            path.startswith("/api/") and not any(c in path for c in "?*#") and ".." not in path
            for path in adapter.read_paths
        ),
        "BEHAVIOR_ADAPTER_UNSUPPORTED",
    )
    capabilities = frozenset(
        cap
        for cap, value in (
            ("task_density_v1", adapter.density),
            ("header_theme_v1", adapter.theme),
            ("coffee_local_summary_v1", adapter.coffee_summary),
        )
        if value is not None
    )

    async def compiled(request: BehaviorDriverInput) -> CompiledAssetWitness:
        witness = _compiled(await resolve_candidate_compilation(request), request.binding)
        result = await _execute_owned_process(
            request,
            witness,
            False,
            executable_path=executable_path,
            launch_args=launch_args,
            adapter=adapter,
            timeout_seconds=worker_timeout_seconds,
            on_closed=on_worker_closed,
        )
        need(type(result) is CompiledAssetWitness, "BEHAVIOR_COMPILED_BINDING_INVALID")
        return cast(CompiledAssetWitness, result)

    async def observed(
        request: BehaviorDriverInput, witness: CompiledAssetWitness
    ) -> BrowserBehaviorObservation:
        result = await _execute_owned_process(
            request,
            witness,
            True,
            executable_path=executable_path,
            launch_args=launch_args,
            adapter=adapter,
            timeout_seconds=worker_timeout_seconds,
            on_closed=on_worker_closed,
        )
        need(type(result) is BrowserBehaviorObservation, "BEHAVIOR_OBSERVATION_UNVERIFIED")
        return cast(BrowserBehaviorObservation, result)

    return ControllerBehaviorDriver(compiled, observed, capabilities)
