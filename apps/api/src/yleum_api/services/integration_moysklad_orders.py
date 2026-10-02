"""Create MAX customer orders without exposing a warehouse token to generated code."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import httpx

from yleum_api.core.errors import ApiError
from yleum_api.services.integration_catalog import moysklad_quantities
from yleum_api.services.integration_operations import SafePreDispatchError

BASE = "https://api.moysklad.ru/api/remap/1.2"


@dataclass(frozen=True)
class PreparedOrder:
    payload: dict[str, Any]
    customer_code: str
    counterparty_id: str | None
    buyer_name: str
    phone: str | None


def _meta(kind: str, identifier: str) -> dict[str, dict[str, str]]:
    return {
        "meta": {
            "href": f"{BASE}/entity/{kind}/{UUID(identifier)}",
            "type": kind,
            "mediaType": "application/json",
        }
    }


def _rows(response: httpx.Response) -> list[dict[str, Any]]:
    if response.status_code in {400, 401, 403, 404, 412}:
        raise ApiError("integration_request_rejected", "МойСклад отклонил заказ", 422)
    if response.status_code == 429 or response.status_code >= 500:
        raise ApiError("integration_provider_unavailable", "МойСклад временно недоступен", 503)
    if response.status_code >= 300:
        raise ApiError("integration_provider_failed", "Не удалось обратиться к МойСклад", 502)
    try:
        rows = response.json()["rows"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ApiError(
            "integration_response_invalid", "МойСклад вернул неверный ответ", 502
        ) from exc
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ApiError("integration_response_invalid", "МойСклад вернул неверный ответ", 502)
    return rows


async def _entity_id(
    client: httpx.AsyncClient, kind: str, configured: str | None = None
) -> str | None:
    if configured:
        identifier = str(UUID(configured))
        response = await client.get(f"{BASE}/entity/{kind}/{identifier}")
        if response.status_code >= 300:
            _rows(response)
        try:
            if str(UUID(response.json()["id"])) != identifier:
                raise ValueError("mismatched entity")
        except (KeyError, TypeError, ValueError) as exc:
            raise ApiError(
                "integration_configuration_invalid", "Проверьте настройки склада", 409
            ) from exc
        return identifier
    rows = _rows(await client.get(f"{BASE}/entity/{kind}", params={"limit": 2}))
    if len(rows) != 1:
        raise ApiError(
            "integration_configuration_required",
            "Выберите организацию и склад в настройках подключения МойСклад",
            409,
        )
    try:
        return str(UUID(rows[0]["id"]))
    except (ValueError, KeyError, TypeError) as exc:
        raise ApiError(
            "integration_response_invalid", "МойСклад вернул неверный ответ", 502
        ) from exc


def _price(row: dict[str, Any]) -> float:
    prices = row.get("salePrices")
    if not isinstance(prices, list) or not prices or not isinstance(prices[0], dict):
        raise ApiError("integration_request_rejected", "У товара нет цены продажи", 422)
    try:
        value = Decimal(str(prices[0]["value"]))
    except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
        raise ApiError(
            "integration_response_invalid", "МойСклад вернул неверную цену", 502
        ) from exc
    if not value.is_finite() or value < 0:
        raise ApiError("integration_response_invalid", "МойСклад вернул неверную цену", 502)
    return float(value)


async def _existing_counterparty(client: httpx.AsyncClient, customer_code: str) -> str | None:
    counterparties = _rows(
        await client.get(
            f"{BASE}/entity/counterparty",
            params={"filter": f"externalCode={customer_code}", "limit": 2},
        )
    )
    if len(counterparties) > 1:
        raise ApiError("integration_response_invalid", "Найдено несколько покупателей", 502)
    if counterparties:
        try:
            return str(UUID(counterparties[0]["id"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise ApiError("integration_response_invalid", "Неверный покупатель", 502) from exc
    return None


async def prepare_customer_order(
    client: httpx.AsyncClient,
    *,
    project_id: UUID,
    max_user_id: int,
    client_key: str,
    buyer_name: str,
    phone: str | None,
    lines: list[tuple[UUID, Decimal]],
    organization_id: str | None = None,
    store_id: str | None = None,
) -> PreparedOrder:
    organization = await _entity_id(client, "organization", organization_id)
    assert organization is not None
    store = await _entity_id(client, "store", store_id)
    totals: dict[UUID, Decimal] = {}
    for identifier, quantity in lines:
        totals[identifier] = totals.get(identifier, Decimal(0)) + quantity
    ids = [str(identifier) for identifier in totals]
    products: dict[str, dict[str, Any]] = {}
    for product_id in ids:
        response = await client.get(f"{BASE}/entity/product/{product_id}")
        if response.status_code >= 300:
            _rows(response)
        try:
            product = response.json()
            if str(UUID(product["id"])) != product_id:
                raise ValueError("mismatched product")
        except (ValueError, KeyError, TypeError) as exc:
            raise ApiError("integration_response_invalid", "Товар МойСклад не найден", 502) from exc
        products[product_id] = product
    stock_filter = "assortmentId=" + ",".join(ids)
    if store:
        stock_filter += ";storeId=" + store
    stock = await client.get(
        f"{BASE}/report/stock/all/current",
        params={
            "stockType": "freeStock",
            "include": "zeroLines",
            "filter": stock_filter,
        },
    )
    if stock.status_code >= 300:
        _rows(stock)
    quantities = moysklad_quantities(stock.json())
    for identifier, requested in totals.items():
        available = quantities.get(str(identifier))
        if available is None or Decimal(str(available)) < requested:
            raise ApiError("integration_request_rejected", "Товара недостаточно на складе", 422)

    customer_code = (
        "yleum-max-" + hashlib.sha256(f"{project_id}:{max_user_id}".encode()).hexdigest()[:32]
    )
    counterparty_id = await _existing_counterparty(client, customer_code)
    positions = [
        {
            "assortment": _meta("product", str(identifier)),
            "quantity": float(quantity),
            "reserve": float(quantity),
            "price": _price(products[str(identifier)]),
        }
        for identifier, quantity in totals.items()
    ]
    payload: dict[str, Any] = {
        "organization": _meta("organization", organization),
        "positions": positions,
        "externalCode": "yleum-order-"
        + hashlib.sha256(f"{project_id}:{max_user_id}:{client_key}".encode()).hexdigest()[:32],
        "description": "Заказ из мини-приложения MAX через Yleum",
    }
    if store:
        payload["store"] = _meta("store", store)
    return PreparedOrder(payload, customer_code, counterparty_id, buyer_name, phone)


async def submit_customer_order(
    client: httpx.AsyncClient, prepared: PreparedOrder
) -> dict[str, str]:
    buyer_name = prepared.buyer_name.strip()
    phone = prepared.phone.strip() or None if prepared.phone is not None else None
    counterparty_id = prepared.counterparty_id
    if counterparty_id is None:
        # Another order may have created the buyer after our read-only preflight.
        # The caller holds a per-buyer PostgreSQL advisory lock around this check.
        try:
            counterparty_id = await _existing_counterparty(client, prepared.customer_code)
        except ApiError as exc:
            raise SafePreDispatchError(exc.code, exc.message, exc.status_code) from exc
        except httpx.RequestError as exc:
            raise SafePreDispatchError(
                "integration_provider_unavailable",
                "МойСклад временно недоступен. Повторите заказ позже.",
                503,
            ) from exc
    if counterparty_id is None:
        body: dict[str, str] = {
            "name": buyer_name,
            "externalCode": prepared.customer_code,
        }
        if phone:
            body["phone"] = phone
        created = await client.post(f"{BASE}/entity/counterparty", json=body)
        if created.status_code >= 300:
            _rows(created)
        try:
            counterparty_id = str(UUID(created.json()["id"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise ApiError(
                "integration_response_invalid", "Неверный ответ о покупателе", 502
            ) from exc

    # Keep this order's submitted contact independent of the shared actor profile.
    description = [
        "Заказ из мини-приложения MAX через Yleum",
        f"Покупатель: {buyer_name}",
    ]
    if phone:
        description.append(f"Телефон: {phone}")
    payload = {
        **prepared.payload,
        "agent": _meta("counterparty", counterparty_id),
        "description": "\n".join(description),
    }
    response = await client.post(f"{BASE}/entity/customerorder", json=payload)
    if response.status_code >= 300:
        _rows(response)
    try:
        result = response.json()
        return {"provider": "moysklad", "id": str(UUID(result["id"]))}
    except (ValueError, KeyError, TypeError) as exc:
        raise ApiError("integration_response_invalid", "МойСклад не подтвердил заказ", 502) from exc


async def create_customer_order(
    client: httpx.AsyncClient,
    *,
    project_id: UUID,
    max_user_id: int,
    client_key: str,
    buyer_name: str,
    phone: str | None,
    lines: list[tuple[UUID, Decimal]],
    organization_id: str | None = None,
    store_id: str | None = None,
) -> dict[str, str]:
    prepared = await prepare_customer_order(
        client,
        project_id=project_id,
        max_user_id=max_user_id,
        client_key=client_key,
        buyer_name=buyer_name,
        phone=phone,
        lines=lines,
        organization_id=organization_id,
        store_id=store_id,
    )
    return await submit_customer_order(client, prepared)
