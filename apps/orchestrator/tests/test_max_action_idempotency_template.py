"""Static contract for exact-once signed MAX functional-gate writes."""

from __future__ import annotations

from pathlib import Path

_TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "max-miniapp-nextjs"


def test_managed_actions_dedupe_only_explicit_proof_requests() -> None:
    route = (_TEMPLATE / "src/app/api/omnia/actions/route.ts").read_text(encoding="utf-8")

    assert 'request.headers.get("x-omnia-proof-key")' in route
    assert "regex(/^[a-f0-9]{64}$/)" in route
    assert ".onConflictDoNothing({" in route
    assert (
        "target: [schema.maxBusinessActions.maxUserId, "
        "schema.maxBusinessActions.idempotencyKey]" in route
    )
    assert "deduplicated: true" in route
    assert "action.actionType !== input.actionType" in route
    assert "canonicalJson(action.payload) !== canonicalJson(input.payload)" in route
    assert 'error: "Idempotency key payload conflict"' in route
    assert "idempotencyKey: null" not in route


def test_managed_action_idempotency_schema_is_upgrade_safe() -> None:
    schema = (_TEMPLATE / "src/lib/db/schema.ts").read_text(encoding="utf-8")
    migration = (_TEMPLATE / "drizzle/0002_action_idempotency.sql").read_text(encoding="utf-8")
    init_db = (_TEMPLATE / "scripts/init-db.mjs").read_text(encoding="utf-8")

    assert 'idempotencyKey: text("idempotency_key")' in schema
    assert 'uniqueIndex("max_business_actions_user_idempotency_key_uq")' in schema
    for source in (migration, init_db):
        assert 'ADD COLUMN IF NOT EXISTS "idempotency_key" text' in source
        assert (
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            '"max_business_actions_user_idempotency_key_uq"' in source
        )


def test_signed_proof_allows_only_real_read_only_integration_checks() -> None:
    route = (_TEMPLATE / "src/app/api/omnia/integrations/[...path]/route.ts").read_text(
        encoding="utf-8"
    )

    assert 'request.headers.get("x-omnia-proof-key")' in route
    assert "PROOF_KEY_RE.test(proofKey)" in route
    assert 'request.headers.get("x-omnia-proof-authorization")' in route
    assert "PROOF_AUTHORIZATION_RE.test(proofAuthorization)" in route
    assert "proofKey.length > 0 || proofAuthorization.length > 0" in route
    assert 'code: "proof_authorization_invalid"' in route
    assert 'method: "GET"' in route
    assert '"X-Omnia-MAX-Preview-Capability": previewCapability' in route
    assert '"X-Omnia-Proof-Key": proofKey' in route
    assert '"X-Omnia-Proof-Authorization": proofAuthorization' in route
    assert 'const PROOF_KEY_BOUND_HEADER = "x-omnia-proof-key-bound"' in route
    assert "proofKeyIsBound(validation, proofKey)" in route
    assert "timingSafeEqual(expected, actual)" in route
    assert '!["status", "catalog"].includes(operation)' in route
    assert 'code: "external_verification_unavailable"' in route
    assert 'model: "proof-sandbox"' not in route
    assert 'provider: "proof-sandbox"' not in route


def test_signed_proof_requires_actual_provider_and_marks_owner_dependencies_retryable() -> None:
    route = (_TEMPLATE / "src/app/api/omnia/integrations/[...path]/route.ts").read_text(
        encoding="utf-8"
    )

    assert 'catalog: ["iiko", "moysklad"]' in route
    assert 'leads: ["bitrix24", "amocrm"]' in route
    assert 'payments: ["yookassa"]' in route
    assert '"payment-status": ["yookassa"]' in route
    assert "requiredProviders.length === 0" in route
    assert "proofProviderRequired(operation, providers)" in route
    assert 'code: "integration_required"' in route
    assert "status: 409" in route
    assert "validation.status >= 500" in route
    assert "proofInfrastructureUnavailable()" in route
    assert 'code: "proof_infrastructure_unavailable"' in route
    assert 'const PROOF_INFRASTRUCTURE_HEADER = "X-Omnia-Proof-Infrastructure"' in route
    assert "[PROOF_INFRASTRUCTURE_HEADER]: \"unavailable\"" in route
    assert 'const PROOF_OWNER_DEPENDENCY_HEADER = "X-Omnia-Proof-Owner-Dependency"' in route
    assert "[PROOF_OWNER_DEPENDENCY_HEADER]: \"required\"" in route
