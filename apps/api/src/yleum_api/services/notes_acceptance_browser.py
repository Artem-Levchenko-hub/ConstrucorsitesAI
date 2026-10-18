"""Installed notes-only probe. Never selected by generated metadata or a model.

One synthetic record per durable attempt; an unknown attempt is never replayed.
Fresh-context observation is deliberately not a database durability receipt.
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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from yleum_api.services import max_behavior_browser as browser_tools
from yleum_api.services import max_behavior_proof as behavior
from yleum_api.services.generation.notes_acceptance import CHECKS, PROFILE, need


def runner_digest() -> str:
    base = Path(__file__).parent
    names = (
        "notes_acceptance_browser.py",
        "notes_acceptance_worker.py",
        "max_behavior_browser_worker.py",
        "max_behavior_browser.py",
    )
    return behavior.digest(
        {
            name: hashlib.sha256((base / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for name in names
        }
    )


@dataclass(frozen=True)
class NotesDriver:
    collect_compiled: Any
    executable_path: str
    launch_args: tuple[str, ...] = ()
    supported_capabilities: frozenset[str] = frozenset({PROFILE})

    async def observe(self, request: Any, witness: Any, operation_id: UUID) -> dict[str, bool]:
        origin, bootstrap = browser_tools._origin(request)
        payload = {
            "origin": origin,
            "bootstrap": bootstrap,
            "operation_id": str(operation_id),
            "assets": [asdict(item) for item in witness.assets],
            "platform_assets": [asdict(item) for item in witness.platform_assets],
            "executable_path": self.executable_path,
            "launch_args": list(self.launch_args),
            "timeout_seconds": 35,
            "runner_sha256": runner_digest(),
        }
        return await _owned_observation(payload)


class _NotesProcess(browser_tools._OwnedProcess):
    def __init__(self) -> None:
        # Keep the old named worker and its pinned bytes untouched. The new
        # entrypoint reuses its exact guardian/reaping implementation.
        environment = dict(os.environ)
        environment.update(
            DEBUG="",
            PWDEBUG="0",
            PYTHONINSPECT="",
            PYTHONSTARTUP="",
            PYTHONPATH=browser_tools._API_SOURCE_ROOT,
        )
        self.process = subprocess.Popen(
            [sys.executable, "-m", "yleum_api.services.notes_acceptance_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            start_new_session=True,
            env=environment,
        )
        try:
            self.stdin = browser_tools._PrivatePipe(self.process.stdin)
            self.stdout = browser_tools._PrivatePipe(self.process.stdout)
            self.exited = browser_tools._PrivatePipe(
                os.fdopen(os.pidfd_open(self.process.pid), "rb", buffering=0)
            )
        except BaseException:
            self.process.kill()
            self.process.wait(timeout=1)
            raise
        self.pid = self.process.pid


async def _owned_observation(payload: dict[str, Any]) -> dict[str, bool]:
    raw = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
    need(len(raw) <= 131072, "notes private input limit")
    process, group = _NotesProcess(), 0

    async def close() -> bool:
        process.stdin.close()
        if process.returncode is None:
            process.send_signal(signal.SIGTERM)
        try:
            async with asyncio.timeout(3):
                await process.wait()
        except TimeoutError:
            for owned in (group, process.pid):
                if owned:
                    try:
                        os.killpg(owned, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            async with asyncio.timeout(1):
                await process.wait()
        async with asyncio.timeout(1):
            tail = await process.stdout.read(131073)
        messages = [json.loads(line) for line in tail.splitlines()]
        return bool(
            process.returncode == 0
            and len(tail) <= 131072
            and messages
            and messages[-1].get("cleanup_closed") is True
        )

    closed = False
    try:
        async with asyncio.timeout(5):
            hello = json.loads(await process.stdout.readline())
            need(hello == {"guardian": process.pid, "ready": True}, "notes guardian unavailable")
            await process.stdin.write(raw)
            process.stdin.close()
            hello = json.loads(await process.stdout.readline())
            need(
                set(hello) == {"guardian", "owned_group"}
                and hello["guardian"] == process.pid
                and type(hello["owned_group"]) is int
                and hello["owned_group"] > 1,
                "notes guardian unavailable",
            )
            group = hello["owned_group"]
        async with asyncio.timeout(40):
            result = json.loads(await process.stdout.readline())
            await process.wait()
        closed = result.get("cleanup_closed") is True and process.returncode == 0
        need(
            closed
            and set(result) == {"status", "outcomes", "cleanup_closed"}
            and result["status"] == "PASS",
            "notes browser unconfirmed",
        )
        from yleum_api.services.generation.notes_acceptance import validate_outcomes

        validate_outcomes(result["outcomes"])
        return {key: True for key in result["outcomes"]}
    finally:
        if not closed:
            cleanup = asyncio.create_task(close())
            deadline = time.monotonic() + 6
            while not cleanup.done() and time.monotonic() < deadline:
                try:
                    await asyncio.wait({cleanup}, timeout=max(0, deadline - time.monotonic()))
                except asyncio.CancelledError:
                    continue
            if not cleanup.done():
                cleanup.cancel()
            else:
                try:
                    closed = cleanup.result()
                except Exception:
                    pass
        process.close_pipes()
        need(closed, "notes browser cleanup unconfirmed")


def observe_notes(payload: dict[str, Any]) -> dict[str, bool]:
    """Real DOM input + exact scoped network actions; no page-reported PASS."""
    from playwright.sync_api import sync_playwright

    need(payload["runner_sha256"] == runner_digest(), "notes runner changed")
    token = "yleum-acceptance-" + str(UUID(payload["operation_id"]))
    origin, bootstrap = payload["origin"], payload["bootstrap"]
    approved = {item["path"]: item for item in payload["assets"]}
    need(approved and all(path.startswith("/") for path in approved), "notes compilation absent")
    # This first profile has no third-party assets or external communication.
    need(not payload["platform_assets"], "notes external assets unsupported")
    deadline = time.monotonic() + 30
    failures: list[str] = []
    allowed_write: dict[str, Any] = {}
    writes: list[dict[str, Any]] = []
    seen: set[str] = set()
    requests = 0

    def read_rows(context: Any) -> list[dict[str, str]]:
        response = context.request.get(
            origin + "/api/notes",
            timeout=3000,
            max_redirects=0,
            headers={"Cache-Control": "no-cache"},
        )
        need(response.status == 200 and len(response.body()) <= 1048576, "notes read unavailable")
        rows = response.json()
        need(type(rows) is list and len(rows) <= 1000, "notes read shape")
        need(
            all(
                type(row) is dict
                and set(row) == {"id", "title", "body"}
                and all(type(value) is str for value in row.values())
                and str(UUID(row["id"])) == row["id"]
                for row in rows
            ),
            "notes row shape",
        )
        need(len({row["id"] for row in rows}) == len(rows), "notes ambiguous rows")
        return sorted(rows, key=lambda row: row["id"])

    def route_request(route: Any) -> None:
        nonlocal requests
        incoming = route.request
        path = urlsplit(incoming.url).path
        requests += 1
        try:
            need(
                time.monotonic() < deadline and requests <= 160 and incoming.url == origin + path,
                "notes request scope",
            )
            if incoming.method == "GET":
                need(path in {"/", "/api/notes", *approved}, "notes request scope")
            else:
                need(
                    bool(allowed_write)
                    and incoming.method == allowed_write["method"]
                    and path == allowed_write["path"],
                    "notes write scope",
                )
                actual = incoming.post_data_json if incoming.post_data else None
                need(actual == allowed_write["body"], "notes write payload changed")
                allowed_write.clear()  # one request, never a hidden retry
            response = route.fetch(timeout=3000, max_redirects=0)
            body = response.body()
            need(response.status == 200 and len(body) <= 1048576, "notes response unavailable")
            if path in approved:
                asset = approved[path]
                need(
                    len(body) == asset["bytes"]
                    and hashlib.sha256(body).hexdigest() == asset["sha256"],
                    "notes served compilation changed",
                )
                seen.add(path)
            if incoming.method != "GET":
                writes.append(response.json())
            route.fulfill(response=response, body=body)
        except Exception:
            failures.append("request")
            route.abort()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=payload["executable_path"],
            headless=True,
            timeout=10000,
            args=payload["launch_args"],
        )
        contexts: list[Any] = []
        try:

            def page_context() -> tuple[Any, Any]:
                context = browser.new_context(
                    service_workers="block", viewport={"width": 390, "height": 844}
                )
                contexts.append(context)
                browser_tools._authenticate(context, bootstrap)
                context.route("**/*", route_request)

                def socket(value: Any) -> None:
                    failures.append("websocket")
                    value.close()

                context.route_web_socket("**/*", socket)
                page = context.new_page()
                page.set_default_timeout(2000)
                page.on("pageerror", lambda _error: failures.append("pageerror"))
                seen.clear()
                page.goto(origin + "/", wait_until="networkidle", timeout=5000)
                need(
                    any(path.endswith(".js") for path in seen) and not failures,
                    "notes served compilation not observed",
                )
                return context, page

            context, page = page_context()
            baseline = read_rows(context)
            need(not any(token in row["title"] for row in baseline), "notes attempt already exists")
            title, body = token, "Проверка сохранения • αβ"
            page.get_by_test_id("notes-title").fill(title)
            page.get_by_test_id("notes-body").fill(body)
            allowed_write.update(
                method="POST", path="/api/notes", body={"title": title, "body": body}
            )
            page.get_by_test_id("notes-save").click()
            page.get_by_text(title, exact=True).wait_for(state="visible")
            need(len(writes) == 1 and not failures, "notes create unconfirmed")
            created = writes[0]
            need(
                type(created) is dict
                and set(created) == {"id", "title", "body"}
                and created["title"] == title
                and created["body"] == body
                and str(UUID(created["id"])) == created["id"]
                and created["id"] not in {row["id"] for row in baseline},
                "notes create binding",
            )
            identifier = created["id"]

            def row_on(current: Any, expected: str) -> Any:
                row = current.locator(f'[data-testid="notes-row"][data-note-id="{identifier}"]')
                row.wait_for(state="visible")
                row.get_by_text(title, exact=True).wait_for(state="visible")
                row.get_by_text(expected, exact=True).wait_for(state="visible")
                return row

            row = row_on(page, body)
            need(created in read_rows(context), "notes server read missing")
            row.get_by_test_id("notes-edit").click()
            changed = body + " — изменено"
            page.get_by_test_id("notes-body").fill(changed)
            allowed_write.update(
                method="PATCH",
                path="/api/notes/" + identifier,
                body={"title": title, "body": changed},
            )
            page.get_by_test_id("notes-save").click()
            row_on(page, changed)
            page.reload(wait_until="networkidle")
            row_on(page, changed)
            second, fresh = page_context()
            row = row_on(fresh, changed)
            expected = {"id": identifier, "title": title, "body": changed}
            need(expected in read_rows(second), "notes fresh-context read missing")
            allowed_write.update(method="DELETE", path="/api/notes/" + identifier, body=None)
            row.get_by_test_id("notes-delete").click()
            row.wait_for(state="detached")
            third, final = page_context()
            need(
                final.locator(f'[data-note-id="{identifier}"]').count() == 0
                and read_rows(third) == baseline
                and len(writes) == 3
                and not failures,
                "notes deletion or preservation unconfirmed",
            )
            return dict.fromkeys(CHECKS, True)
        finally:
            for context in contexts:
                context.close()
            browser.close()
