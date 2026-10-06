import yleum_api.models  # noqa: F401
from yleum_api.models.base import Base


def test_ledger_tables_are_known_to_future_alembic_autogeneration():
    assert {
        "provider_ledger_entries",
        "provider_ledger_conflicts",
        "provider_ledger_confirmations",
    } <= set(Base.metadata.tables)
    confirmations = Base.metadata.tables["provider_ledger_confirmations"]
    assert {column.name for column in confirmations.primary_key.columns} == {
        "organization_id",
        "operation_id",
    }
    assert "provider_calls.id" in {key.target_fullname for key in confirmations.foreign_keys}
    assert all(key.ondelete != "CASCADE" for key in confirmations.foreign_keys)
