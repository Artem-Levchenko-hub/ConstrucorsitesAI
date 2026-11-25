from asyncio import CancelledError
from time import perf_counter

from prometheus_client import CollectorRegistry, Histogram
from sqlalchemy import event
from sqlalchemy.engine import Engine, ExceptionContext, ExecutionContext
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REGISTRY = CollectorRegistry()
_HTTP_DURATION = Histogram(
    "yleum_http_request_duration_seconds",
    "API requests observed through the last response body frame.",
    ("method", "route", "status"),
    registry=REGISTRY,
)
_DB_DURATION = Histogram(
    "yleum_db_query_duration_seconds",
    "PostgreSQL statement execution time in the API process.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
    registry=REGISTRY,
)
_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"})
_TIMER = "_yleum_sql_timer"


class MonitoringMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        status = "500"
        observed = False
        method = scope["method"]

        def observe() -> None:
            route = getattr(scope.get("route"), "path", "__unmatched__")
            _HTTP_DURATION.labels(method if method in _METHODS else "OTHER", route, status).observe(
                perf_counter() - started
            )

        async def track(message: Message) -> None:
            nonlocal status, observed
            if message["type"] == "http.response.start":
                status = str(message["status"])
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                observe()
                observed = True

        try:
            await self.app(scope, receive, track)
        except CancelledError:
            status = "499"
            raise
        finally:
            if not observed:
                observe()


def _finish_query(context: ExecutionContext | None) -> None:
    if context is not None and (started := context.__dict__.pop(_TIMER, None)) is not None:
        _DB_DURATION.observe(perf_counter() - started)


def instrument_database(engine: Engine) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def before(*args: object) -> None:
        context = args[4]
        if isinstance(context, ExecutionContext):
            context.__dict__[_TIMER] = perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def after(*args: object) -> None:
        context = args[4]
        if isinstance(context, ExecutionContext):
            _finish_query(context)

    @event.listens_for(engine, "handle_error")
    def failed(exception: ExceptionContext) -> None:
        _finish_query(exception.execution_context)
