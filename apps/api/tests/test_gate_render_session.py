"""Characterisation of the browser session every url/files gate leg runs.

Frozen BEFORE the session moved into ``render_settle`` and unchanged AFTER: nine
gates carried a hand-rolled copy of "launch chromium → context → page → settle →
audit → close, abstain on any failure". Seven of them left with the site builder;
the two that stay are pinned here. The assertions describe what a caller can
observe, not how the code is laid out.

No chromium: ``playwright.async_api.async_playwright`` is a recorder, and each
gate's ``_audit_page`` is a stub, so only the session itself is under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import unquote, urlparse

import pytest

from yleum_api.services import chip_pixel_gate, wow_dom_gate

RESOLVER_ARGS = ["--host-resolver-rules=MAP preview.test 10.0.0.7"]
SESSION_STATE = {"cookies": [{"name": "authjs.session-token", "value": "x"}], "origins": []}
PAGE_SET = {"index.html": "<h1>Главная</h1>", "pages/about.html": "<p>О нас</p>"}


@dataclass(frozen=True)
class Leg:
    gate: ModuleType
    abstain: Callable[[int], Any]
    timeout_ms: int = 15_000
    resolver: bool = False
    init_script: str | None = None
    prefix: str | None = None  # None → the gate has no files leg
    needs_session: bool = False
    extra: tuple[Any, ...] = ()  # positional arguments after url/files

    @property
    def name(self) -> str:
        return self.gate.__name__.rsplit(".", 1)[-1]


LEGS = (
    Leg(
        wow_dom_gate,
        lambda w: wow_dom_gate.WowDomReport((), w, 0, (), rendered=False),
        prefix="omnia-wowdom-",
    ),
    Leg(
        chip_pixel_gate,
        lambda w: chip_pixel_gate.FidelityReport((), rendered=False),
        prefix="omnia-chippix-",
        extra=(chip_pixel_gate.FidelitySpec(),),
    ),
)
FILE_LEGS = tuple(leg for leg in LEGS if leg.prefix is not None)


def _ids(legs: tuple[Leg, ...]) -> list[str]:
    return [leg.name for leg in legs]


# ── recorder ──────────────────────────────────────────────────────────────────


class Recorder:
    def __init__(self, *, navigation_fails: bool = False, playwright_fails: bool = False) -> None:
        self.navigation_fails = navigation_fails
        self.playwright_fails = playwright_fails
        self.launches: list[dict[str, Any]] = []
        self.contexts: list[dict[str, Any]] = []
        self.events: list[tuple[Any, ...]] = []
        self.open_browsers = 0
        self.open_contexts = 0
        self.navigated: list[_Page] = []
        self.tree_at_navigation: dict[str, str] | None = None
        self.workdir: Path | None = None

    def __call__(self) -> Recorder:
        if self.playwright_fails:
            raise RuntimeError("chromium is not installed")
        return self

    async def __aenter__(self) -> Recorder:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    @property
    def chromium(self) -> Recorder:
        return self

    async def launch(self, **kwargs: Any) -> _Browser:
        self.launches.append(kwargs)
        self.open_browsers += 1
        return _Browser(self)


class _Browser:
    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    async def new_context(self, **kwargs: Any) -> _Context:
        return _Context(self.recorder, kwargs)

    async def new_page(self, **kwargs: Any) -> _Page:
        # Playwright: the page owns a fresh context; closing the page closes it.
        return await _Context(self.recorder, kwargs).new_page()

    async def close(self) -> None:
        self.recorder.open_browsers -= 1


class _Context:
    def __init__(self, recorder: Recorder, kwargs: dict[str, Any]) -> None:
        self.recorder = recorder
        self.closed = False
        recorder.contexts.append(kwargs)
        recorder.open_contexts += 1

    async def new_page(self) -> _Page:
        return _Page(self.recorder, self)

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.recorder.open_contexts -= 1


class _Page:
    def __init__(self, recorder: Recorder, context: _Context) -> None:
        self.recorder = recorder
        self.context = context
        self.url = "about:blank"

    async def add_init_script(self, script: str) -> None:
        self.recorder.events.append(("init_script", script))

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.recorder.events.append(("goto", url, kwargs))
        if url.startswith("file://"):
            index = Path(unquote(urlparse(url).path))
            self.recorder.workdir = index.parent
            self.recorder.tree_at_navigation = {
                str(path.relative_to(index.parent)): path.read_text(encoding="utf-8")
                for path in sorted(index.parent.rglob("*"))
                if path.is_file()
            }
        if self.recorder.navigation_fails:
            raise RuntimeError("net::ERR_CONNECTION_REFUSED")
        self.url = url
        self.recorder.navigated.append(self)

    async def wait_for_load_state(self, state: str, **kwargs: Any) -> None:
        self.recorder.events.append(("settle", state))

    async def evaluate(self, expression: str, *args: Any) -> None:
        self.recorder.events.append(("settle", "fonts"))

    async def wait_for_timeout(self, ms: int) -> None:
        self.recorder.events.append(("settle", "paint"))

    async def close(self) -> None:
        await self.context.close()


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Recorder]:
    def install(**kwargs: bool) -> Recorder:
        recorder = Recorder(**kwargs)
        monkeypatch.setattr("playwright.async_api.async_playwright", recorder)
        return recorder

    return install


class Audit:
    """Stands in for the gate's ``_audit_page``: returns a sentinel, records the page."""

    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.report = object()
        self.pages: list[Any] = []
        self.extra: list[tuple[Any, ...]] = []

    async def __call__(self, page: Any, *extra: Any) -> object:
        page.recorder.events.append(("audit",))
        self.pages.append(page)
        self.extra.append(extra)
        if self.fails:
            raise RuntimeError("page.evaluate: execution context was destroyed")
        return self.report


def _stub(monkeypatch: pytest.MonkeyPatch, leg: Leg, **kwargs: bool) -> Audit:
    audit = Audit(**kwargs)
    monkeypatch.setattr(leg.gate, "_audit_page", audit)
    if leg.resolver:
        monkeypatch.setattr(leg.gate, "preview_resolver_args", lambda: list(RESOLVER_ARGS))
    return audit


def _launch_args(recorder: Recorder) -> list[str] | None:
    assert len(recorder.launches) == 1
    launch = recorder.launches[0]
    assert launch["headless"] is True
    assert set(launch) <= {"headless", "args"}
    return launch.get("args")


SETTLED_READ = [
    ("settle", "load"),
    ("settle", "networkidle"),
    ("settle", "fonts"),
    ("settle", "paint"),
    ("audit",),
]


def _everything_closed(recorder: Recorder) -> bool:
    return recorder.open_browsers == 0 and recorder.open_contexts == 0


def _navigation(recorder: Recorder) -> tuple[Any, ...]:
    (navigation,) = [event for event in recorder.events if event[0] == "goto"]
    return navigation


def _url_kwargs(leg: Leg, **kwargs: Any) -> dict[str, Any]:
    if leg.needs_session:
        kwargs.setdefault("storage_state", SESSION_STATE)
    return kwargs


# ── url leg ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("leg", LEGS, ids=_ids(LEGS))
def test_url_leg_audits_one_settled_page_and_closes_the_session(leg, session, monkeypatch):
    recorder = session()
    audit = _stub(monkeypatch, leg)

    report = asyncio.run(
        leg.gate.audit_url(
            "https://app.test/p/shop",
            *leg.extra,
            width=777,
            timeout_ms=4_321,
            storage_state=SESSION_STATE,
        )
    )

    assert report is audit.report
    assert _launch_args(recorder) == (RESOLVER_ARGS if leg.resolver else None)
    assert recorder.contexts == [
        {
            "viewport": {"width": 777, "height": leg.gate.GATE_HEIGHT},
            "reduced_motion": "reduce",
            "storage_state": SESSION_STATE,
        }
    ]
    navigation = {"wait_until": "domcontentloaded", "timeout": 4_321}
    expected: list[tuple[Any, ...]] = [("goto", "https://app.test/p/shop", navigation)]
    if leg.init_script is not None:
        expected.insert(0, ("init_script", leg.init_script))
    # The page is read only after load → network idle → fonts → paint beat.
    assert recorder.events == expected + SETTLED_READ
    assert audit.pages == recorder.navigated
    assert audit.extra == [leg.extra]
    assert _everything_closed(recorder)


@pytest.mark.parametrize("leg", LEGS, ids=_ids(LEGS))
def test_url_leg_defaults(leg, session, monkeypatch):
    recorder = session()
    _stub(monkeypatch, leg)

    asyncio.run(leg.gate.audit_url("https://app.test/", *leg.extra, **_url_kwargs(leg)))

    (context,) = recorder.contexts
    assert context["viewport"] == {"width": leg.gate.GATE_WIDTH, "height": leg.gate.GATE_HEIGHT}
    assert context["storage_state"] == (SESSION_STATE if leg.needs_session else None)
    assert _navigation(recorder)[2]["timeout"] == leg.timeout_ms


@pytest.mark.parametrize("leg", LEGS, ids=_ids(LEGS))
def test_url_leg_abstains_when_navigation_fails(leg, session, monkeypatch, caplog):
    recorder = session(navigation_fails=True)
    audit = _stub(monkeypatch, leg)

    with caplog.at_level("WARNING", logger=leg.gate.__name__):
        report = asyncio.run(
            leg.gate.audit_url("https://down.test/", *leg.extra, **_url_kwargs(leg, width=640))
        )

    assert report == leg.abstain(640)
    assert report.rendered is False
    assert audit.pages == []
    assert _everything_closed(recorder)
    assert [(r.name, r.getMessage()) for r in caplog.records] == [
        (
            leg.gate.__name__,
            f"{leg.name}: url audit failed (abstain): "
            "RuntimeError('net::ERR_CONNECTION_REFUSED')",
        )
    ]


@pytest.mark.parametrize("leg", LEGS, ids=_ids(LEGS))
def test_url_leg_abstains_when_the_page_audit_fails(leg, session, monkeypatch):
    recorder = session()
    _stub(monkeypatch, leg, fails=True)

    report = asyncio.run(
        leg.gate.audit_url("https://app.test/", *leg.extra, **_url_kwargs(leg, width=640))
    )

    assert report == leg.abstain(640)
    assert _everything_closed(recorder)


@pytest.mark.parametrize("leg", LEGS, ids=_ids(LEGS))
def test_url_leg_abstains_when_the_browser_cannot_start(leg, session, monkeypatch):
    recorder = session(playwright_fails=True)
    _stub(monkeypatch, leg)

    report = asyncio.run(
        leg.gate.audit_url("https://app.test/", *leg.extra, **_url_kwargs(leg, width=640))
    )

    assert report == leg.abstain(640)
    assert recorder.launches == []


@pytest.mark.parametrize("leg", FILE_LEGS, ids=_ids(FILE_LEGS))
def test_files_leg_renders_the_materialised_page_set_and_removes_it(leg, session, monkeypatch):
    recorder = session()
    audit = _stub(monkeypatch, leg)

    report = asyncio.run(
        leg.gate.audit_files(dict(PAGE_SET), *leg.extra, width=777, timeout_ms=4_321)
    )

    assert report is audit.report
    assert _launch_args(recorder) == (RESOLVER_ARGS if leg.resolver else None)
    (context,) = recorder.contexts
    assert context["viewport"] == {"width": 777, "height": leg.gate.GATE_HEIGHT}
    assert context["reduced_motion"] == "reduce"
    assert context.get("storage_state") is None
    assert set(context) <= {"viewport", "reduced_motion", "storage_state"}

    navigation = _navigation(recorder)
    assert navigation[1].startswith("file://") and navigation[1].endswith("/index.html")
    assert navigation[2] == {"wait_until": "domcontentloaded", "timeout": 4_321}
    expected_scripts = [] if leg.init_script is None else [("init_script", leg.init_script)]
    assert recorder.events == [*expected_scripts, navigation, *SETTLED_READ]

    assert recorder.tree_at_navigation == PAGE_SET
    assert recorder.workdir is not None
    assert recorder.workdir.name.startswith(leg.prefix)
    assert not recorder.workdir.exists()
    assert audit.pages == recorder.navigated
    assert audit.extra == [leg.extra]
    assert _everything_closed(recorder)


@pytest.mark.parametrize("leg", FILE_LEGS, ids=_ids(FILE_LEGS))
def test_files_leg_defaults(leg, session, monkeypatch):
    recorder = session()
    _stub(monkeypatch, leg)

    asyncio.run(leg.gate.audit_files(dict(PAGE_SET), *leg.extra))

    (context,) = recorder.contexts
    assert context["viewport"] == {"width": leg.gate.GATE_WIDTH, "height": leg.gate.GATE_HEIGHT}
    assert _navigation(recorder)[2]["timeout"] == leg.timeout_ms


@pytest.mark.parametrize("leg", FILE_LEGS, ids=_ids(FILE_LEGS))
def test_files_leg_without_an_index_never_starts_a_browser(leg, session, monkeypatch):
    recorder = session()
    audit = _stub(monkeypatch, leg)

    report = asyncio.run(leg.gate.audit_files({"about.html": "<p>x</p>"}, *leg.extra, width=640))

    assert report == leg.abstain(640)
    assert recorder.launches == []
    assert audit.pages == []


@pytest.mark.parametrize("leg", FILE_LEGS, ids=_ids(FILE_LEGS))
def test_files_leg_abstains_when_navigation_fails(leg, session, monkeypatch, caplog):
    recorder = session(navigation_fails=True)
    audit = _stub(monkeypatch, leg)

    with caplog.at_level("WARNING", logger=leg.gate.__name__):
        report = asyncio.run(leg.gate.audit_files(dict(PAGE_SET), *leg.extra, width=640))

    assert report == leg.abstain(640)
    assert audit.pages == []
    assert recorder.workdir is not None and not recorder.workdir.exists()
    assert _everything_closed(recorder)
    assert [(r.name, r.getMessage()) for r in caplog.records] == [
        (
            leg.gate.__name__,
            f"{leg.name}: files audit failed (abstain): "
            "RuntimeError('net::ERR_CONNECTION_REFUSED')",
        )
    ]


@pytest.mark.parametrize("leg", FILE_LEGS, ids=_ids(FILE_LEGS))
def test_files_leg_abstains_when_the_page_audit_fails(leg, session, monkeypatch):
    recorder = session()
    _stub(monkeypatch, leg, fails=True)

    report = asyncio.run(leg.gate.audit_files(dict(PAGE_SET), *leg.extra, width=640))

    assert report == leg.abstain(640)
    assert recorder.workdir is not None and not recorder.workdir.exists()
    assert _everything_closed(recorder)


@pytest.mark.parametrize("leg", FILE_LEGS, ids=_ids(FILE_LEGS))
def test_files_leg_abstains_when_the_browser_cannot_start(leg, session, monkeypatch):
    recorder = session(playwright_fails=True)
    _stub(monkeypatch, leg)

    report = asyncio.run(leg.gate.audit_files(dict(PAGE_SET), *leg.extra, width=640))

    assert report == leg.abstain(640)
    assert recorder.launches == []
