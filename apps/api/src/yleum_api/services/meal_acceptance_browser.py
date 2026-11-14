"""Fixed, prepublication meal observation against the owning candidate preview."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
import unicodedata
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any, cast
from urllib.parse import urlsplit

from playwright.sync_api import TimeoutError as BrowserTimeout
from playwright.sync_api import expect, sync_playwright

from yleum_api.services.behavior_platform_assets import PLATFORM_ASSET_LOCATIONS
from yleum_api.services.generation.meal_acceptance import PACK_AMOUNT_KOPECKS, PACK_SKU
from yleum_api.services.generation.recovery_contract import RecoveryContractError
from yleum_api.services.max_behavior_browser import _authenticate
from yleum_api.services.max_behavior_proof import _compiled_asset_path
from yleum_api.services.meal_acceptance_entitlement import MealEntitlementFailure
from yleum_api.services.meal_pdf_content import MAX_PDF_BYTES, MealPdfError, verify_meal_pdf_bytes

BUILD_CHECKS = (
    "navigation", "three_meals", "shop_locked", "shopping_add", "shopping_toggle",
    "shopping_reload", "shopping_fresh_context", "pdf_download", "preserve_existing",
)
EDIT_CHECKS = (*BUILD_CHECKS, "shopping_filters", "shopping_remaining_count")
CHECKOUT_WIRING = "checkout_wiring"
_MANAGED_ORDER = "/api/omnia/business/orders"
_INTEGRATION = "/api/omnia/integrations/order-checkout"
_ENTITLEMENT = "/api/meal/entitlement"
_CHECKOUT_PATHS = (
    _MANAGED_ORDER, _INTEGRATION, _INTEGRATION + "-status",
    _INTEGRATION + "-receipt", _ENTITLEMENT,
)
_ORDER_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.ASCII,
)
_SHOPPING = "/api/meal/shopping"
_PDF = "/api/meal/pdf"
_SESSION = "/api/max/session"
_PUBLIC_CONFIG = "/api/omnia/config"
_MEAL_CATALOG = "/api/meal/catalog"
_SIMPLE_ID = re.compile(r"[A-Za-z0-9_-]{1,96}\Z")
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_WAIT_MS = 2200


class MealObservationFailed(RecoveryContractError):
    """A settled candidate mismatch, safe to persist as a bounded repair reason."""

    def __init__(self, check: str) -> None:
        if check not in (*EDIT_CHECKS, CHECKOUT_WIRING):
            raise ValueError("unsupported meal check")
        super().__init__(check)
        self.check = check


def _need(condition: object) -> None:
    if not condition:
        raise RecoveryContractError("meal browser observation unconfirmed")


def _match(condition: object, check: str) -> None:
    if not condition:
        raise MealObservationFailed(check)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        _need(name not in result)
        result[name] = value
    return result


def _rows(raw: bytes) -> dict[str, dict[str, Any]]:
    return _row_inventory(json.loads(raw, object_pairs_hook=_unique_object))


def _row_inventory(values: Any) -> dict[str, dict[str, Any]]:
    _need(type(values) is list and len(values) <= 200)
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        _need(type(value) is dict and set(value) == {"id", "ingredient_id", "name", "bought"})
        row, ingredient = value["id"], value["ingredient_id"]
        name, bought = value["name"], value["bought"]
        _need(
            type(row) is str and _SIMPLE_ID.fullmatch(row) and row not in result
            and type(ingredient) is str and _SIMPLE_ID.fullmatch(ingredient)
            and type(name) is str and 0 < len(name) <= 160 and type(bought) is bool
        )
        result[row] = value
    return result


def _expected_label(value: str, check: str) -> str:
    text = " ".join(unicodedata.normalize("NFC", value).split())
    _match(0 < len(text) <= 160 and _CYRILLIC.search(text), check)
    return text


def _asset_inventory(payload: dict[str, Any]) -> dict[str, tuple[int, str]]:
    approved: dict[str, tuple[int, str]] = {}
    origin = payload["origin"]
    for group in ("assets", "platform_assets"):
        records = payload[group]
        _need(type(records) is list and len(records) <= 128)
        for record in records:
            name, length, digest = record["path"], record["bytes"], record["sha256"]
            # The fixed platform registry is the only authority for a public URL.
            # A compiled asset must be on the candidate origin.
            _need(type(name) is str and (
                _compiled_asset_path(name) if group == "assets"
                else name in PLATFORM_ASSET_LOCATIONS
            ))
            url = origin + name if name.startswith("/") else name
            _need(
                url not in approved and type(length) is int and 0 < length <= MAX_PDF_BYTES
                and type(digest) is str and _DIGEST.fullmatch(digest)
            )
            approved[url] = (length, digest)
    _need(any(url.startswith(origin + "/_next/") and url.endswith(".js") for url in approved))
    return approved


def _json_request(request: Any, limit: int) -> dict[str, Any]:
    raw = request.post_data
    _need(type(raw) is str and len(raw) <= limit and request.headers.get(
        "content-type", "",
    ).split(";", 1)[0].lower() == "application/json")
    value = json.loads(raw, object_pairs_hook=_unique_object)
    _need(type(value) is dict)
    return cast(dict[str, Any], value)


class _CheckoutWiring:
    """Only the browser's calls and their order are observed here."""

    def __init__(self, order: object, actor: str, origin: str) -> None:
        _need(type(order) is str and _ORDER_ID.fullmatch(order))
        self.order = order
        self.actor = actor
        self.origin = origin
        self.position = 0
        self.allowed = 0
        self.invalid = False
        self.config_version: int | None = None
        # The SDK accepts this shape. It has no valid claims or signature.
        self.receipt = f"v1.{secrets.token_urlsafe(18)}.{'A' * 86}"

    def observe_catalog(self, response: Any, body: bytes) -> None:
        try:
            _need(response.status == 200 and response.headers.get(
                "content-type", "",
            ).split(";", 1)[0].lower() == "application/json")
            value = json.loads(body, object_pairs_hook=_unique_object)
            _need(
                type(value) is dict and set(value) == {"configVersion", "sku", "unitPriceKopecks"}
            )
            version = value["configVersion"]
            _need(
                type(version) is int and 0 <= version <= 2**53 - 1
                and value["sku"] == PACK_SKU
                and type(value["unitPriceKopecks"]) is int
                and value["unitPriceKopecks"] == PACK_AMOUNT_KOPECKS
                and (self.config_version is None or self.config_version == version)
            )
        except (RecoveryContractError, ValueError, RecursionError):
            self.invalid = True
        else:
            self.config_version = version

    def _managed_order(self, request: Any, body: dict[str, Any]) -> dict[str, Any]:
        _need(request.headers.get("x-max-expected-actor") == self.actor)
        _need(set(body) == {"configVersion", "operationKey", "customerName", "items"})
        version, key, items = body["configVersion"], body["operationKey"], body["items"]
        _need(
            type(version) is int and version == self.config_version
            and type(key) is str and _ORDER_ID.fullmatch(key.lower())
            and body["customerName"] == "Покупатель" and type(items) is list and len(items) == 1
        )
        item = items[0]
        _need(
            type(item) is dict
            and item == {
                "catalogId": PACK_SKU, "quantity": 1, "unitPriceKopecks": PACK_AMOUNT_KOPECKS,
            }
            and type(item["quantity"]) is int and type(item["unitPriceKopecks"]) is int
        )
        return {
            "id": self.order, "resource": "orders", "created_at": "2026-01-01T00:00:00Z",
            "revision": "0" * 64, "status": "new", "customer_name": "Покупатель",
            "items": [{
                "catalog_id": PACK_SKU, "title": "Рацион на месяц", "quantity": 1,
                "unit_price_kopecks": PACK_AMOUNT_KOPECKS,
            }],
            "total_kopecks": PACK_AMOUNT_KOPECKS, "currency": "RUB",
        }

    def _reply(self, request: Any, path: str) -> dict[str, Any]:
        body = _json_request(request, 2048)
        if path == _MANAGED_ORDER:
            return self._managed_order(request, body)
        if path == _ENTITLEMENT:
            _need(body == {"receipt": self.receipt})
            return {"granted": True}
        _need(set(body) == {"expectedActor", "payload"} and body["expectedActor"] == self.actor)
        values = body["payload"]
        _need(type(values) is dict)
        expected = {"order_id": self.order}
        if path == _INTEGRATION and "return_url" in values:
            _need(values["return_url"] in (self.origin, self.origin + "/"))
            expected["return_url"] = values["return_url"]
        _need(values == expected)
        if path == _INTEGRATION + "-receipt":
            return {"receipt": self.receipt}
        pending = path == _INTEGRATION
        return {
            "order_id": self.order, "status": "pending" if pending else "succeeded",
            "confirmation_url": None, "failure_reason": None, "test": True,
            "paid_at": None if pending else "2026-01-01T00:00:00Z",
        }

    def intercept(self, route: Any, path: str, *, same_origin: bool) -> None:
        try:
            request = route.request
            _need(
                same_origin and request.method == "POST" and not self.invalid
                and self.position < self.allowed and path == _CHECKOUT_PATHS[self.position]
            )
            reply = self._reply(request, path)
        except (RecoveryContractError, ValueError, RecursionError):
            self.invalid = True
            route.fulfill(status=400, json={"error": {"code": "invalid_request"}})
            return
        self.position += 1
        route.fulfill(status=201 if path == _MANAGED_ORDER else 200, json=reply)

    def verify(self) -> None:
        _match(not self.invalid and self.position == len(_CHECKOUT_PATHS), CHECKOUT_WIRING)


class _MealBrowser:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.origin = payload["origin"]
        parsed = urlsplit(self.origin)
        # The owner validates the actual preview URL before launching this
        # worker. Only loopback HTTP is admitted for the local Chromium fixture.
        _need(
            not parsed.username and not parsed.password and parsed.hostname is not None
            and self.origin == f"{parsed.scheme}://{parsed.netloc}"
            and (parsed.scheme == "https" or (
                parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            ))
        )
        self.cookie = payload.get("buyer_cookie")
        self.buyer = payload.get("buyer_id")
        if self.cookie is None:
            # A generic owner preview can contain the owner's existing list.
            # Closed production wiring requires a privately minted QA buyer.
            _need(parsed.scheme == "http" and type(payload.get("bootstrap")) is str
                  and payload["bootstrap"].startswith(self.origin + "/"))
        else:
            _need(parsed.scheme == "https" and type(self.cookie) is str
                  and type(self.buyer) is str and _SIMPLE_ID.fullmatch(self.buyer)
                  and 0 < len(self.cookie) <= 4096
                  and not any(char in self.cookie for char in ("\r", "\n", ";")))
        self.checkout: _CheckoutWiring | None = None
        if payload.get("check_checkout_wiring") is True:
            if self.cookie is None or not isinstance(self.buyer, str):
                raise RecoveryContractError("meal browser observation unconfirmed")
            self.checkout = _CheckoutWiring(payload.get("order_id"), self.buyer, self.origin)
        self.assets = _asset_inventory(payload)
        self.seen: set[str] = set()
        self.actor_seen = False
        self.contexts: list[Any] = []
        self.deadline = time.monotonic() + 28
        self.request_count = 0
        self.response_bytes = 0
        self.failed = False
        self.write: tuple[str, str, dict[str, Any]] | None = None
        self.settled_write = False
        self.pdf_allowed = False
        self.created: set[str] = set()

    def _bounded(self, response: Any, limit: int, statuses: set[int]) -> bytes:
        _need(response.status in statuses and time.monotonic() < self.deadline)
        declared = response.headers.get("content-length")
        if declared is not None:
            _need(declared.isdecimal() and len(declared) <= 10 and int(declared) <= limit)
        body = response.body()
        if not isinstance(body, bytes):
            raise RecoveryContractError("meal browser response type unconfirmed")
        self.response_bytes += len(body)
        _need(len(body) <= limit and self.response_bytes <= 48 * 1024 * 1024)
        return body

    def _route(self, route: Any) -> None:
        request = route.request
        self.request_count += 1
        path = urlsplit(request.url).path
        pin = self.assets.get(request.url)
        same_origin = request.url == self.origin + path
        try:
            _need(not self.failed and self.request_count <= 160
                  and time.monotonic() < self.deadline)
            if path in _CHECKOUT_PATHS:
                if self.checkout is None:
                    raise RecoveryContractError("meal browser observation unconfirmed")
                self.checkout.intercept(route, path, same_origin=same_origin)
                return
            catalog = same_origin and path == _MEAL_CATALOG and self.checkout is not None
            if request.method == "GET":
                _need(pin is not None or (same_origin and (
                    path in {"/", _SHOPPING, _SESSION, _PUBLIC_CONFIG}
                    or (path == _PDF and self.pdf_allowed) or catalog
                )))
                if path == _PDF:
                    self.pdf_allowed = False
            else:
                expected = self.write
                _need(same_origin and expected is not None
                      and (request.method, path) == expected[:2])
                assert expected is not None
                actual = _json_request(request, 256)
                key = next(iter(expected[2]))
                _need(
                    actual == expected[2]
                    and type(actual[key]) is type(expected[2][key])
                )
                self.write = None  # Only this exact synthetic mutation may be dispatched.
            options: dict[str, Any] = {}
            if not same_origin:
                # No signed preview auth, cookies, referer or candidate-controlled
                # header is ever forwarded off origin for the pinned SDK bytes.
                options["headers"] = {"Accept": "application/javascript"}
            response = route.fetch(timeout=3000, max_redirects=0, max_retries=0, **options)
            limit = pin[0] if pin is not None else MAX_PDF_BYTES if path == _PDF else 1024 * 1024
            statuses = {200, 201, 204} if request.method != "GET" else {200}
            if catalog and self.checkout is not None:
                body = self._bounded(response, 4096, {200, 404})
                self.checkout.observe_catalog(response, body)
                if self.checkout.invalid:
                    route.fulfill(status=400, json={"error": {"code": "invalid_request"}})
                    return
            else:
                body = self._bounded(response, limit, statuses)
            if pin is not None:
                length, digest = pin
                _need(len(body) == length and hashlib.sha256(body).hexdigest() == digest)
                self.seen.add(request.url)
            if path == _SESSION and self.cookie is not None:
                profile = json.loads(body, object_pairs_hook=_unique_object)
                _need(type(profile) is dict and profile.get("mode") == "acceptance"
                      and type(profile.get("user")) is dict
                      and profile["user"].get("id") == self.buyer)
                self.actor_seen = True
            if path == _PDF:
                media = response.headers.get("content-type", "").split(";", 1)[0]
                _need(media == "application/pdf")
            if request.method != "GET":
                self.settled_write = True
            route.fulfill(response=response, body=body)
        except Exception:
            self.failed = True
            route.abort()

    def _socket(self, socket: Any) -> None:
        self.failed = True
        socket.close()

    def _healthy(self) -> None:
        _need(not self.failed and time.monotonic() < self.deadline)

    def _compiled(self) -> None:
        self._healthy()
        _need(any(url.startswith(self.origin + "/_next/") and url.endswith(".js")
                  for url in self.seen))
        _need(self.cookie is None or self.actor_seen)

    def browser_page(self, browser: Any) -> tuple[Any, Any]:
        context = browser.new_context(
            service_workers="block", accept_downloads=True, viewport={"width": 390, "height": 844},
        )
        self.contexts.append(context)
        if self.cookie is None:
            _authenticate(context, self.payload["bootstrap"])
        else:
            context.add_cookies([{
                "name": "__Host-max_session", "value": self.cookie,
                "url": self.origin + "/", "secure": True, "httpOnly": True,
            }])
        context.route("**/*", self._route)
        context.route_web_socket("**/*", self._socket)
        page = context.new_page()
        page.set_default_timeout(_WAIT_MS)

        def page_error(_error: Any) -> None:
            self.failed = True

        page.on("pageerror", page_error)
        self.seen.clear()
        self.actor_seen = False
        page.goto(self.origin + "/", wait_until="networkidle", timeout=5000)
        self._compiled()
        return context, page

    def read_rows(self, context: Any) -> dict[str, dict[str, Any]]:
        self._healthy()
        response = context.request.get(
            self.origin + _SHOPPING, timeout=3000, max_redirects=0, max_retries=0,
            headers={"Cache-Control": "no-cache"},
        )
        return _rows(self._bounded(response, 1024 * 1024, {200}))

    @contextmanager
    def step(self, name: str) -> Any:
        self._healthy()
        try:
            yield
        except (AssertionError, BrowserTimeout):
            self._healthy()
            raise MealObservationFailed(name) from None
        self._healthy()

    def request_change(
        self, page: Any, target: Any, method: str, path: str, body: dict[str, Any], check: str,
    ) -> None:
        expect(target).to_be_visible(timeout=_WAIT_MS)
        self.write = (method, path, body)
        self.settled_write = False
        try:
            try:
                target.click()
            except BrowserTimeout:
                # Once armed, a click timeout may follow a dispatched write.
                # Preserve uncertainty instead of allowing a model repair/replay.
                self.failed = True
                self._healthy()
                raise
            end = min(self.deadline, time.monotonic() + 3)
            while not self.settled_write and not self.failed and time.monotonic() < end:
                page.wait_for_timeout(20)
            self._healthy()
            if self.write is None:
                _need(self.settled_write)  # Request dispatched; outcome cannot be inferred.
            else:
                raise MealObservationFailed(check)
        finally:
            self.write = None

    def cleanup(self, context: Any, baseline: dict[str, dict[str, Any]]) -> None:
        # Only rows positively attributed to this observation may be removed.
        self._healthy()
        for row in sorted(self.created):
            response = context.request.delete(
                self.origin + _SHOPPING + "/" + row, timeout=3000,
                max_redirects=0, max_retries=0,
            )
            self._bounded(response, 4096, {200, 204})
        _need(self.read_rows(context) == baseline)


def _observe_checkout(probe: _MealBrowser, page: Any, wiring: _CheckoutWiring) -> None:
    with probe.step(CHECKOUT_WIRING):
        for selector, stop in (("meal-buy-pack", 2), ("meal-check-checkout", len(_CHECKOUT_PATHS))):
            button = page.get_by_test_id(selector)
            expect(button).to_be_visible(timeout=_WAIT_MS)
            _match(not wiring.invalid, CHECKOUT_WIRING)
            wiring.allowed = stop
            button.click()
            end = min(probe.deadline, time.monotonic() + _WAIT_MS / 1000)
            while (wiring.position < stop and not wiring.invalid and not probe.failed
                   and time.monotonic() < end):
                page.wait_for_timeout(20)
            probe._healthy()
            _match(not wiring.invalid and wiring.position == stop, CHECKOUT_WIRING)


def _go(page: Any, tab: str) -> None:
    page.get_by_test_id("meal-nav-" + tab).click()


def _collect_meals(page: Any) -> tuple[list[str], list[str], dict[str, tuple[str, str]]]:
    dishes: list[str] = []
    ingredients: list[str] = []
    selections: dict[str, tuple[str, str]] = {}
    for meal in ("breakfast", "lunch", "dinner"):
        _go(page, "plan")
        page.get_by_test_id("meal-open-" + meal).click()
        dish = page.get_by_test_id("meal-dish-name")
        dish.wait_for(state="visible")
        dishes.append(_expected_label(dish.inner_text(), "three_meals"))
        labels = page.get_by_test_id("meal-ingredient-name")
        buttons = page.get_by_test_id("meal-add-ingredient")
        labels.first.wait_for(state="visible")
        _match(1 <= labels.count() == buttons.count() <= 20, "three_meals")
        for index in range(labels.count()):
            label, button = labels.nth(index), buttons.nth(index)
            _match(label.is_visible() and button.is_visible(), "three_meals")
            name = _expected_label(label.inner_text(), "three_meals")
            identifier = button.get_attribute("data-ingredient-id") or ""
            _match(_SIMPLE_ID.fullmatch(identifier), "three_meals")
            ingredients.append(name)
            if identifier not in selections:
                selections[identifier] = (name, meal)
    _match(len({dish.casefold() for dish in dishes}) == 3, "three_meals")
    _match(len({name.casefold() for name, _ in selections.values()}) >= 2, "three_meals")
    return dishes, ingredients, selections


def _pick_row(page: Any, row: str) -> Any:
    return page.locator(f'[data-testid="meal-shopping-row"][data-item-id="{row}"]')


def _visible_rows(page: Any, expected: dict[str, dict[str, Any]]) -> None:
    rows = page.get_by_test_id("meal-shopping-row").filter(visible=True)
    expect(rows).to_have_count(len(expected), timeout=_WAIT_MS)
    for identifier, record in expected.items():
        row = _pick_row(page, identifier)
        expect(row).to_be_visible(timeout=_WAIT_MS)
        expect(row).to_contain_text(record["name"], timeout=_WAIT_MS)
        toggle = row.get_by_test_id("meal-shopping-toggle")
        expect(toggle).to_be_visible(timeout=_WAIT_MS)
        wanted = str(record["bought"]).lower()
        if toggle.get_attribute("aria-checked") in {"true", "false"}:
            expect(toggle).to_have_attribute("aria-checked", wanted, timeout=_WAIT_MS)
        elif toggle.get_attribute("aria-pressed") in {"true", "false"}:
            expect(toggle).to_have_attribute("aria-pressed", wanted, timeout=_WAIT_MS)
        elif record["bought"]:
            expect(toggle).to_be_checked(timeout=_WAIT_MS)
        else:
            expect(toggle).not_to_be_checked(timeout=_WAIT_MS)


def _verify_download(probe: _MealBrowser, page: Any, dishes: list[str], names: list[str]) -> None:
    probe.pdf_allowed = True
    try:
        with page.expect_download(timeout=4000) as event:
            page.get_by_test_id("meal-download-pdf").click()
        download = event.value
        _need(
            download.url == probe.origin + _PDF
            or download.url.startswith("blob:" + probe.origin + "/")
        )
        _need(download.failure() is None)
        path = download.path()
        with path.open("rb") as stream:
            data = stream.read(MAX_PDF_BYTES + 1)
        try:
            verify_meal_pdf_bytes(data, dishes=dishes, ingredients=names)
        except MealPdfError:
            raise MealObservationFailed("pdf_download") from None
        finally:
            download.delete()
    finally:
        probe.pdf_allowed = False


def _observe(
    probe: _MealBrowser, browser: Any, mode: str, on_ready: Callable[[], None] | None,
) -> list[dict[str, Any]] | None:
    retain = probe.payload.get("retain_build_fixture", False)
    raw_prior = probe.payload.get("prior_shopping")
    _need(type(retain) is bool and (not retain or (mode == "build" and raw_prior is None)))
    _need(mode != "edit" or raw_prior is not None)
    prior = None
    if raw_prior is not None:
        prior = _row_inventory(raw_prior)
        _need(len(prior) == 2
              and len({row["ingredient_id"] for row in prior.values()}) == 2
              and {row["bought"] for row in prior.values()} == {True, False})
    context, page = probe.browser_page(browser)
    with probe.step("navigation"):
        for tab, label in (("plan", "Рацион"), ("shopping", "Покупки"), ("store", "Магазин")):
            control = page.get_by_test_id("meal-nav-" + tab)
            expect(control).to_be_visible(timeout=_WAIT_MS)
            expect(control).to_have_accessible_name(label, timeout=_WAIT_MS)
    with probe.step("shop_locked"):
        _go(page, "store")
        info = page.get_by_test_id("meal-pack-info")
        expect(info).to_be_visible(timeout=_WAIT_MS)
        _match(bool(info.inner_text().strip()), "shop_locked")
    if probe.checkout is not None:
        _observe_checkout(probe, page, probe.checkout)
    with probe.step("three_meals"):
        dishes, names, selections = _collect_meals(page)
    with probe.step("pdf_download"):
        _go(page, "plan")
        _verify_download(probe, page, dishes, names)
    baseline = probe.read_rows(context)
    if prior is not None:
        with probe.step("preserve_existing"):
            _match(all(baseline.get(key) == value for key, value in prior.items()),
                   "preserve_existing")
            _go(page, "shopping")
            _visible_rows(page, baseline)
    occupied = {item["ingredient_id"] for item in baseline.values()}
    selected: dict[str, tuple[str, str]] = {}
    used_names: set[str] = set()
    for key, value in selections.items():
        name = value[0].casefold()
        if key not in occupied and name not in used_names:
            selected[key] = value
            used_names.add(name)
        if len(selected) == 2:
            break
    # Existing data must not be toggled or removed to manufacture a test row.
    _need(len(selected) == 2)
    expected = dict(baseline)
    retained: list[dict[str, Any]] | None = None
    try:
        with probe.step("shopping_add"):
            for ingredient, (name, meal) in selected.items():
                _go(page, "plan")
                page.get_by_test_id("meal-open-" + meal).click()
                button = page.locator(
                    f'[data-testid="meal-add-ingredient"][data-ingredient-id="{ingredient}"]',
                )
                probe.request_change(
                    page, button, "POST", _SHOPPING, {"ingredient_id": ingredient}, "shopping_add",
                )
                rows = probe.read_rows(context)
                added = rows.keys() - expected.keys()
                _match(len(added) == 1, "shopping_add")
                key = next(iter(added))
                record = rows[key]
                _match(record["ingredient_id"] == ingredient and record["name"] == name,
                       "shopping_add")
                probe.created.add(key)
                _match(not record["bought"], "shopping_add")
                expected[key] = record
                _match(rows == expected, "preserve_existing")
            _go(page, "shopping")
            _visible_rows(page, expected)
        first, second = [key for key in expected if key in probe.created]

        def toggle(key: str, *, context: Any, page: Any) -> None:
            control = _pick_row(page, key).get_by_test_id("meal-shopping-toggle")
            probe.request_change(
                page, control, "PATCH", _SHOPPING + "/" + key, {"bought": True}, "shopping_toggle",
            )
            expected[key] = {**expected[key], "bought": True}
            _match(probe.read_rows(context) == expected, "shopping_toggle")

        with probe.step("shopping_toggle"):
            toggle(first, context=context, page=page)
            _visible_rows(page, expected)
        with probe.step("shopping_reload"):
            probe.seen.clear()
            probe.actor_seen = False
            page.reload(wait_until="networkidle", timeout=5000)
            probe._compiled()
            _go(page, "shopping")
            _match(probe.read_rows(context) == expected, "shopping_reload")
            _visible_rows(page, expected)
        context, page = probe.browser_page(browser)
        with probe.step("shopping_fresh_context"):
            _go(page, "shopping")
            _match(probe.read_rows(context) == expected, "shopping_fresh_context")
            _visible_rows(page, expected)
        if mode == "edit":
            unbought = {key: value for key, value in expected.items() if not value["bought"]}
            counter = page.get_by_test_id("meal-remaining-count")
            with probe.step("shopping_filters"):
                all_filter = page.get_by_test_id("meal-filter-all")
                unbought_filter = page.get_by_test_id("meal-filter-unbought")
                expect(all_filter).to_have_accessible_name("Все", timeout=_WAIT_MS)
                expect(unbought_filter).to_have_accessible_name("Не куплено", timeout=_WAIT_MS)
                all_filter.click()
                _visible_rows(page, expected)
                unbought_filter.click()
                _visible_rows(page, unbought)
            with probe.step("shopping_remaining_count"):
                expect(counter).to_have_text(str(len(unbought)), timeout=_WAIT_MS)
                toggle(second, context=context, page=page)
                del unbought[second]
                expect(counter).to_have_text(str(len(unbought)), timeout=_WAIT_MS)
                _visible_rows(page, unbought)
                page.get_by_test_id("meal-filter-all").click()
                _visible_rows(page, expected)
        if probe.checkout is not None:
            probe.checkout.verify()
        if on_ready is not None:
            probe._healthy()
            try:
                on_ready()
            except MealEntitlementFailure:
                raise
            except Exception:
                # Failed transport can still hold an in-flight entitlement POST.
                probe.failed = True
                raise
        if retain:
            _need(probe.read_rows(context) == expected)
            retained = [dict(expected[key]) for key in sorted(probe.created)]
    finally:
        if not probe.failed and retained is None:
            probe.cleanup(context, baseline)
    if prior is not None:
        # Rebind the same rows on restart and carry them through later edits.
        return [dict(prior[key]) for key in sorted(prior)]
    return retained


def observe_meal(
    payload: dict[str, Any], *, capture: dict[str, Any] | None = None,
    on_ready: Callable[[], None] | None = None,
) -> dict[str, bool]:
    """Observe an owned preview, fulfilling all checkout traffic inside the browser."""
    _need(payload.get("mode") in {"build", "edit"})
    _need(not payload.get("retain_build_fixture") or capture is not None)
    try:
        probe = _MealBrowser(payload)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=payload["executable_path"], headless=True,
                timeout=10000, args=payload["launch_args"],
            )
            try:
                shopping = _observe(probe, browser, payload["mode"], on_ready)
            finally:
                for context in probe.contexts:
                    context.close()
                browser.close()
    except (RecoveryContractError, MealEntitlementFailure):
        raise
    except Exception:
        # Playwright's text may include private URLs or DOM.
        raise RecoveryContractError("meal browser observation unconfirmed") from None
    probe._healthy()
    checks = EDIT_CHECKS if payload["mode"] == "edit" else BUILD_CHECKS
    if probe.checkout is not None:
        # A late request may have raced with confirmed real fixture persistence.
        _need(not probe.checkout.invalid)
        checks = (*checks, CHECKOUT_WIRING)
    if capture is not None and shopping is not None:
        capture["shopping"] = shopping
    return dict.fromkeys(checks, True)
