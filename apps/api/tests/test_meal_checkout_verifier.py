"""Exercise the locked MAX verifier with receipts issued by the Python signers."""

import json
import subprocess
from types import SimpleNamespace
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import SecretStr

from yleum_api.services import max_checkout_receipt, max_project_kit
from yleum_api.services.generation.meal_acceptance import (
    PACK_AMOUNT_KOPECKS,
    PACK_SKU,
    fixture_signing_seed,
)

NOW = 1_794_633_600
PROJECT = UUID("11111111-1111-4111-8111-111111111111")
OTHER_PROJECT = UUID("22222222-2222-4222-8222-222222222222")
ORDER = UUID("33333333-3333-4333-8333-333333333333")
ACTOR = "9007199254740993"
FIXTURE_ACTOR = "mealqa_" + "a" * 32
TEST_JWT_SECRET = "synthetic-only-meal-checkout-verifier-secret"

_VERIFY_IN_NODE = """
const { createPublicKey, verify } = require("node:crypto");
const { readFileSync } = require("node:fs");
const { stripTypeScriptTypes } = require("node:module");
const { createContext, SourceTextModule, SyntheticModule } = require("node:vm");
const { source, now, cases } = JSON.parse(readFileSync(0, "utf8"));

async function main() {
  const context = createContext({ Buffer, Date: { now: () => now * 1000 } });
  const verifier = new SourceTextModule(stripTypeScriptTypes(source), { context });
  await verifier.link((specifier) => {
    if (specifier === "server-only") return new SyntheticModule([], () => {}, { context });
    if (specifier !== "node:crypto") throw new Error(`Unexpected import: ${specifier}`);
    return new SyntheticModule(["createPublicKey", "verify"], function () {
      this.setExport("createPublicKey", createPublicKey);
      this.setExport("verify", verify);
    }, { context });
  });
  await verifier.evaluate();
  const verifyReceipt = verifier.namespace.verifyYleumCheckoutReceipt;
  process.stdout.write(JSON.stringify(Object.fromEntries(Object.entries(cases).map(
    ([name, [receipt, expected]]) => [name, verifyReceipt(receipt, expected)]
  ))));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""


def test_locked_verifier_checks_python_receipt_signatures_and_claims(monkeypatch):
    settings = SimpleNamespace(jwt_secret=SecretStr(TEST_JWT_SECRET))
    monkeypatch.setattr(max_checkout_receipt, "get_settings", lambda: settings)
    monkeypatch.setattr(max_project_kit, "get_settings", lambda: settings)

    real_key = max_checkout_receipt._private_key()
    fixture_key = Ed25519PrivateKey.from_private_bytes(
        fixture_signing_seed(PROJECT, TEST_JWT_SECRET),
    )
    foreign_fixture_key = Ed25519PrivateKey.from_private_bytes(
        fixture_signing_seed(OTHER_PROJECT, TEST_JWT_SECRET),
    )
    claims = {
        "purpose": max_checkout_receipt.CHECKOUT_RECEIPT_PURPOSE,
        "project_id": str(PROJECT),
        "actor_id": ACTOR,
        "order_id": str(ORDER),
        "sku": PACK_SKU,
        "quantity": 1,
        "amount_kopecks": PACK_AMOUNT_KOPECKS,
        "currency": "RUB",
        "test": False,
        "iat": NOW,
        "exp": NOW + 300,
    }
    expected = {
        "projectId": str(PROJECT),
        "actorId": ACTOR,
        "sku": PACK_SKU,
        "quantity": 1,
        "amountKopecks": PACK_AMOUNT_KOPECKS,
        "test": False,
    }
    test_claims = {**claims, "test": True}
    test_expected = {**expected, "test": True}
    fixture_claims = {**test_claims, "actor_id": FIXTURE_ACTOR}
    fixture_expected = {**test_expected, "actorId": FIXTURE_ACTOR}
    sign = max_checkout_receipt.sign_checkout_receipt
    real_receipt = sign(claims, private_key=real_key)
    test_receipt = sign(test_claims, private_key=real_key)
    fixture_receipt = sign(fixture_claims, private_key=fixture_key)
    version, payload, _ = test_receipt.split(".")
    _, _, real_signature = real_receipt.split(".")

    valid_cases = {
        "live": (real_receipt, expected),
        "actual_test_shop": (test_receipt, test_expected),
        "isolated_fixture": (fixture_receipt, fixture_expected),
    }
    rejected_cases = {
        "wrong_actor": (real_receipt, {**expected, "actorId": "9007199254740992"}),
        "foreign_project": (real_receipt, {**expected, "projectId": str(OTHER_PROJECT)}),
        "test_as_live": (test_receipt, expected),
        "live_as_test": (real_receipt, test_expected),
        "fixture_as_live": (
            sign({**fixture_claims, "test": False}, private_key=fixture_key),
            {**fixture_expected, "test": False},
        ),
        "modified_signed_payload": (f"{version}.{payload}.{real_signature}", test_expected),
        "wrong_fixture_key": (
            sign(fixture_claims, private_key=foreign_fixture_key), fixture_expected,
        ),
        "fixture_key_for_numeric_actor": (
            sign(test_claims, private_key=fixture_key), test_expected,
        ),
        "real_key_for_fixture_actor": (
            sign(fixture_claims, private_key=real_key), fixture_expected,
        ),
    }
    for name, changes in {
        "expired": {"iat": NOW - 300, "exp": NOW},
        "future": {"iat": NOW + 6, "exp": NOW + 300},
        "excessive_ttl": {"exp": NOW + 301},
        "wrong_sku": {"sku": "other-pack"},
        "wrong_currency": {"currency": "USD"},
        "wrong_amount": {"amount_kopecks": PACK_AMOUNT_KOPECKS + 1},
        "wrong_quantity": {"quantity": 2},
    }.items():
        rejected_cases[name] = (sign({**claims, **changes}, private_key=real_key), expected)

    result = subprocess.run(
        ["node", "--experimental-vm-modules", "-e", _VERIFY_IN_NODE],
        input=json.dumps({
            "source": max_project_kit._checkout_verifier(PROJECT),
            "now": NOW,
            "cases": {**valid_cases, **rejected_cases},
        }),
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    assert json.loads(result.stdout) == {
        "live": claims,
        "actual_test_shop": test_claims,
        "isolated_fixture": fixture_claims,
        **dict.fromkeys(rejected_cases),
    }
