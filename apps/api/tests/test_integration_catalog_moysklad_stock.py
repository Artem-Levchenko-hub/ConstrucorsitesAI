"""MoySklad catalog availability comes from its stock report, not product metadata."""

from uuid import uuid4

import pytest

from yleum_api.core.errors import ApiError
from yleum_api.services.integration_catalog import moysklad_items, moysklad_quantities


def test_stock_report_sets_quantity_and_leaves_unreported_product_unknown() -> None:
    available, missing = str(uuid4()), str(uuid4())
    report = [{"assortmentId": available, "freeStock": 2.5}]
    catalog = {
        "rows": [
            {"id": available, "name": "Есть", "salePrices": [{"value": 12500}]},
            {"id": missing, "name": "Без расчёта", "salePrices": []},
        ]
    }

    items = moysklad_items(catalog, moysklad_quantities(report))

    assert items[0].price == 125
    assert items[0].available is True
    assert items[0].available_quantity == 2.5
    assert items[1].available is None
    assert items[1].available_quantity is None


@pytest.mark.parametrize("quantity", [0, -3])
def test_zero_or_negative_stock_is_not_available(quantity: int) -> None:
    product_id = str(uuid4())
    items = moysklad_items(
        {"rows": [{"id": product_id, "name": "Товар"}]},
        moysklad_quantities([{"assortmentId": product_id, "freeStock": quantity}]),
    )
    assert items[0].available is False
    assert items[0].available_quantity == quantity


@pytest.mark.parametrize(
    "report",
    [
        [{"assortmentId": str(uuid4()), "freeStock": "nan"}],
        [{"assortmentId": str(uuid4())}],
        {"rows": []},
    ],
)
def test_stock_report_rejects_malformed_values(report: object) -> None:
    with pytest.raises(ApiError):
        moysklad_quantities(report)
