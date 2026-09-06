import pytest

from omnia_api.core.errors import ApiError


def test_product_price_is_converted_without_inventing_stock():
    from omnia_api.services.integration_catalog import moysklad_items

    items = moysklad_items(
        {"rows": [{"id": "product-1", "name": "Tea", "salePrices": [{"value": 12550}]}]}
    )
    assert items[0].price == 125.50
    assert items[0].available is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"rows": {}},
        {"rows": [{"name": "missing id"}]},
        {"rows": [{"id": "p1", "salePrices": [{"value": "NaN"}]}]},
    ],
)
def test_invalid_catalog_is_not_reported_as_success(payload):
    from omnia_api.services.integration_catalog import moysklad_items

    with pytest.raises(ApiError) as error:
        moysklad_items(payload)
    assert error.value.status_code == 502


def test_iiko_deleted_items_removed_and_stop_list_not_invented():
    from omnia_api.services.integration_catalog import iiko_items

    items = iiko_items(
        {
            "products": [
                {"id": "gone", "isDeleted": True},
                {"id": "tea", "name": "Tea", "sizePrices": [{"price": {"currentPrice": 125.5}}]},
            ]
        }
    )
    assert [item.id for item in items] == ["tea"]
    assert items[0].available is None
    assert items[0].price == 125.5
