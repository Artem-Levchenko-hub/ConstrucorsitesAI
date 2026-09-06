"""Normalize catalogs without inventing inventory or accepting malformed data."""

from decimal import Decimal, InvalidOperation
from typing import Any

from omnia_api.schemas.integration_runtime import RuntimeCatalogItem
from omnia_api.services.integration_responses import invalid_response


def _rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = payload.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise invalid_response()
    return rows


def _price(value: Any, divisor: int = 1) -> float | None:
    if value is None:
        return None
    try:
        amount = Decimal(str(value)) / divisor
        if not amount.is_finite() or amount < 0:
            raise invalid_response()
        return float(amount)
    except (ValueError, InvalidOperation) as exc:
        raise invalid_response() from exc


def _id(row: dict[str, Any]) -> str:
    identifier = row.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        raise invalid_response()
    return identifier


def moysklad_items(payload: dict[str, Any]) -> list[RuntimeCatalogItem]:
    items = []
    for row in _rows(payload, "rows"):
        prices = row.get("salePrices", [])
        if not isinstance(prices, list) or any(not isinstance(p, dict) for p in prices):
            raise invalid_response()
        items.append(
            RuntimeCatalogItem(
                id=_id(row),
                name=str(row.get("name") or "Товар"),
                description=str(row.get("description") or ""),
                price=_price(prices[0].get("value") if prices else None, 100),
                # /entity/product is catalog data, not a stock report.
                available=None,
            )
        )
    return items


def iiko_items(payload: dict[str, Any]) -> list[RuntimeCatalogItem]:
    items = []
    for row in _rows(payload, "products"):
        identifier = _id(row)
        if row.get("isDeleted") is True:
            continue
        prices = row.get("sizePrices", [])
        images = row.get("imageLinks", [])
        if (
            not isinstance(prices, list)
            or any(not isinstance(p, dict) for p in prices)
            or not isinstance(images, list)
        ):
            raise invalid_response()
        price = prices[0].get("price", {}) if prices else {}
        if not isinstance(price, dict):
            raise invalid_response()
        items.append(
            RuntimeCatalogItem(
                id=identifier,
                name=str(row.get("name") or "Позиция"),
                description=str(row.get("description") or ""),
                price=_price(price.get("currentPrice")),
                # Nomenclature does not attest to the current terminal stop-list.
                available=None,
                image_url=str(images[0]) if images else None,
            )
        )
    return items
