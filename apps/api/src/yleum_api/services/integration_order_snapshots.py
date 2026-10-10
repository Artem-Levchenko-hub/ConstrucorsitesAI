"""Whitelisted immutable submitted prices; never infer unknown money or currency."""

from decimal import Decimal, InvalidOperation
from typing import Any

from yleum_api.core.errors import ApiError


def minor_value(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, str | int | float | Decimal):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return format(number, "f") if number.is_finite() and number >= 0 else None


def submitted_snapshot(
    positions: list[dict[str, Any]],
    products: dict[str, dict[str, Any]],
    currency: str | None,
) -> dict[str, Any]:
    lines = []
    total = Decimal(0)
    for position in positions:
        identifier = position["assortment"]["meta"]["href"].rsplit("/", 1)[-1]
        price = minor_value(position["price"])
        quantity = minor_value(position["quantity"])
        if price is None or quantity is None:
            raise ApiError("integration_response_invalid", "Некорректная цена заказа", 502)
        line_total = Decimal(price) * Decimal(quantity)
        total += line_total
        name = products[identifier].get("name")
        lines.append(
            {
                "product_id": identifier,
                "name": name if isinstance(name, str) else None,
                "quantity": quantity,
                "unit_price_minor": price,
                "total_minor": format(line_total, "f"),
            }
        )
    return {
        "amount_source": "server_submitted",
        "lines": lines,
        "total_minor": format(total, "f"),
        "currency": currency,
    }
