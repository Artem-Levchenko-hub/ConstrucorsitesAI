"""Actionable feedback for genuine missing managed UI connections, without new authority."""

import pytest

from tests.test_max_management_generation import connection_example, connection_files
from yleum_api.services.max_management_generation import (
    ManagedSourceGap,
    management_implemented_capabilities,
    management_source_gap,
)

RESOURCES = [
    ("orders", "createManagedOrder"),
    ("leads", "createManagedLead"),
    ("bookings", "createManagedBooking"),
]


def disconnected_source(resource, *, mounted_create, rendered_history):
    modules = ("catalog", resource)
    source = connection_example(modules)
    if not mounted_create:
        event = f"onSubmit={{submit_{resource}}}"
        assert event in source
        source = source.replace(event, "")
    if not rendered_history:
        history = (f"{{{resource}.map(record => <p key={{record.id}}>"
                   "{record.customer_name} {record.status}</p>)}")
        assert history in source
        source = source.replace(history, "")
    return connection_files(source, modules)


@pytest.mark.parametrize("resource,method", RESOURCES)
@pytest.mark.parametrize("mounted_create,rendered_history", [
    (False, False), (False, True), (True, False), (True, True),
])
def test_feedback_names_only_the_missing_connection(
    resource, method, mounted_create, rendered_history,
):
    files = disconnected_source(
        resource, mounted_create=mounted_create, rendered_history=rendered_history,
    )
    gap = management_source_gap(files)
    if mounted_create and rendered_history:
        assert gap is None
        assert "history" in management_implemented_capabilities(files)
        return
    assert isinstance(gap, ManagedSourceGap)
    assert gap.startswith(f"Connect {method}")
    assert ("Missing mounted create event." in gap) is (not mounted_create)
    assert (f"Missing rendered {resource} history." in gap) is (not rendered_history)
    if not mounted_create:
        assert "form onSubmit" in gap and "button onClick" in gap
        assert "unmounted helper" in gap
    if not rendered_history:
        assert f"getManagedRecords('{resource}').records" in gap
        assert "records.map" in gap and "record.customer_name" in gap
        assert "Loading records alone" in gap
    assert "operationKey" in gap
    assert management_implemented_capabilities(files) == frozenset()


@pytest.mark.parametrize("resource,_method", RESOURCES)
def test_partial_ui_repair_does_not_mint_a_new_obligation_or_budget(resource, _method):
    gaps = [management_source_gap(disconnected_source(
        resource, mounted_create=create, rendered_history=history,
    )) for create, history in [(False, False), (False, True), (True, False)]]
    assert all(isinstance(gap, ManagedSourceGap) for gap in gaps)
    keys = {gap.rejection_key() for gap in gaps if gap is not None}
    assert None not in keys and len(keys) == 1
    for gap in gaps:
        assert gap is not None
        assert gap.with_diagnostic("bounded redacted feedback").rejection_key() in keys
        assert "no extra model or repair budget" in gap
    assert ManagedSourceGap(str(gaps[0])).rejection_key() is None


def test_loading_a_different_resource_does_not_satisfy_lead_history():
    files = disconnected_source("leads", mounted_create=True, rendered_history=True)
    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(
        "getManagedRecords('leads'", "getManagedRecords('orders'",
    )
    gap = management_source_gap(files)
    assert gap is not None
    assert "Missing rendered leads history." in gap
    assert "Missing mounted create event." not in gap
    assert management_implemented_capabilities(files) == frozenset()
