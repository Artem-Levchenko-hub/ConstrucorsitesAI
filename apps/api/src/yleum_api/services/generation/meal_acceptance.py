"""Explicit, versioned interpretation of the two reviewed Meal requests."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from yleum_api.services.max_behavior_proof import digest

PROFILE = "meal_acceptance_v1"
STATE_KEY = "meal_acceptance_v1"
RECEIPT_PREFIX = "meal_acceptance:"
PACK_SKU = "meal-month-pack"
PACK_AMOUNT_KOPECKS = 1250

BUILD_PROMPT = (
    "Сделай MAX Mini App «Рацион на месяц» на русском. В нижней навигации три раздела "
    "«Рацион», «Покупки», «Магазин». В «Рационе» есть бесплатный пример с одним полным днём "
    "на обычных продуктах. Пользователь может открыть блюдо, добавить ингредиент в «Покупки», "
    "отметить его купленным и выгрузить PDF с названием «Рацион на месяц», названиями блюд и "
    "ингредиентов кириллицей. Список и отметки сохраняются после обновления. В «Магазине» один "
    "тестовый набор из десяти уникальных блюд. До подтверждения сервером покупатель видит "
    "описание набора без доступа к закрытым рецептам. После подтверждённой оплаты в тестовом "
    "магазине тот же покупатель получает доступ ровно к десяти блюдам. Повтор статуса платежа "
    "не дублирует выдачу. Используй синтетические блюда и пользователей, тестовую конфигурацию "
    "магазина и серверную проверку статуса, суммы, валюты и владельца заказа. Не сохраняй "
    "реальные контактные и платёжные данные."
)
EDIT_PROMPT = (
    "Добавь в «Покупки» два фильтра «Все» и «Не куплено» и счётчик оставшихся позиций. Сохрани "
    "навигацию, бесплатный пример, экспорт PDF, список и отметки пользователя, а также доступ "
    "к уже полученному тестовому набору. Повтор статуса оплаты по тому же заказу по-прежнему "
    "выдаёт те же десять блюд."
)

SHOP_CHECKS = (
    "entitlement_denied_before_confirmation",
    "entitlement_invalid_receipts_denied",
    "entitlement_ten_recipes",
    "entitlement_repeat",
    "entitlement_actor_isolation",
)


def mode_for_prompt(prompt: str) -> Literal["build", "edit"] | None:
    if prompt == BUILD_PROMPT:
        return "build"
    return "edit" if prompt == EDIT_PROMPT else None


MEAL_AGENT_GUIDE = """MEAL ACCEPTANCE CONTRACT FOR THIS REVIEWED BRIEF
Keep the platform session and managed kit. Use product SQL scoped by getMaxUser().id.
Use the fixed Meal title and avoid eager server actions during boot. Load product data
through the reviewed same-origin GET endpoints instead.
The installed candidate browser checks visible navigation via data-testid meal-nav-plan,
meal-nav-shopping, meal-nav-store (labels Рацион, Покупки, Магазин). Provide three distinct
visible dishes via meal-open-breakfast, meal-open-lunch, meal-open-dinner with meal-dish-name
and paired meal-ingredient-name / meal-add-ingredient with stable data-ingredient-id.
The three cards contain at least four distinct ingredients and the visible meal-download-pdf
downloads one full-day Cyrillic PDF (browser blob or GET /api/meal/pdf). The title is
Рацион на месяц, with all displayed dish and ingredient names. Generate it without a CDN.
GET /api/meal/shopping returns an array of {id,ingredient_id,name,bought}. POST at the same
path with {ingredient_id} creates one scoped row. PATCH /api/meal/shopping/{id} with {bought}
updates the same row, DELETE returns 204 for that own row. Use safe stable ASCII IDs and
server-stored data. Row meal-shopping-row has data-item-id and visible ingredient name,
toggle meal-shopping-toggle exposes checked state. An edit adds meal-filter-all and
meal-filter-unbought, labels Все and Не куплено, and visible meal-remaining-count.
The remaining count's own text is a number. Show a visible nonempty meal-pack-info from
the same origin without loading private recipes on page entry. Keep private recipes out of
the initial
HTML, client code, public assets, and free responses. Define a single managed SKU
meal-month-pack for the test pack, quantity 1, exactly 1250 kopecks in RUB. Use real
managed createManagedOrder / createYleumOrderCheckout only on the meal-buy-pack button.
Set the managed customerName to the literal Покупатель, with no contact fields. Use the
catalog configVersion and a durable operationKey for this one item. Expose GET
/api/meal/catalog as the public projection {configVersion,sku,unitPriceKopecks} of the
managed getYleumServerAppConfig catalog, with no private data. Check the same order
through getYleumOrderCheckout on the explicit meal-check-checkout button (including when
confirmation_url is null). After verified succeeded test=true, call
getYleumOrderCheckoutReceipt(orderId) and submit only
{receipt} to POST /api/meal/entitlement. That server route takes actor from getMaxUser,
project from configured runtime, fixed expected product/quantity/amount/test mode and uses
verifyYleumCheckoutReceipt from @/lib/omnia/checkout-receipt. Store each grant transactionally
and idempotently by actor, pack and order. Use an empty 403/404 for denied private reads
and an empty 400/403/404 for invalid or foreign receipts, without any recipe data.
POST returns 200 for a confirmed grant including a replay of the same receipt. Afterward
GET /api/meal/recipes returns exactly ten distinct {id,title}, GET /api/meal/recipes/{id}
returns exactly {id,title,ingredients,instructions}. Ingredients is a nonempty array
of nonblank strings. Instructions is a nonblank string.
Both reads must refuse access when the authenticated actor lacks the grant. Preserve existing
grants on edit, including refresh of the same order. Open the list via a separate user action
after confirmation. Test runner exercises an authenticated,
isolated fixture using the same verification path. Do not add a QA bypass, second source of
payment state, or commit sample signed receipts/credentials into product code.
"""


def fixture_signing_seed(project_id: UUID | str, jwt_secret: str) -> bytes:
    """Derive a separate fixture key for each project; publish only its public half."""
    secret = jwt_secret.encode()
    if len(secret) < 32:
        raise ValueError("meal_fixture_signing_unavailable")
    domain = b"yleum:max:meal-acceptance-fixture:v1:" + str(UUID(str(project_id))).encode()
    return hmac.digest(secret, domain, "sha256")


def fixture_public_key_pem(project_id: UUID | str, jwt_secret: str) -> str:
    return (
        Ed25519PrivateKey.from_private_bytes(fixture_signing_seed(project_id, jwt_secret))
        .public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )


@dataclass(frozen=True, slots=True)
class MealContract:
    template: str
    request_sha256: str
    manifest_sha256: str
    requirement_id: str
    mode: Literal["build", "edit"]

    @property
    def checks(self) -> tuple[str, ...]:
        from yleum_api.services.meal_acceptance_browser import (
            BUILD_CHECKS,
            CHECKOUT_WIRING,
            EDIT_CHECKS,
        )

        browser = BUILD_CHECKS if self.mode == "build" else EDIT_CHECKS
        return (*browser, CHECKOUT_WIRING, *SHOP_CHECKS)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "profile": PROFILE,
            "template": self.template,
            "request_sha256": self.request_sha256,
            "manifest_sha256": self.manifest_sha256,
            "requirement_id": self.requirement_id,
            "mode": self.mode,
            "checks": list(self.checks),
            "payment_scope": "candidate_scoped_signed_test_fixture",
            "pdf_scope": "downloaded_text",
        }

    @property
    def contract_digest(self) -> str:
        return digest(self.to_json())


def reviewed_meal_contract(run: Any, template: str) -> MealContract | None:
    from yleum_api.services.generation.recovery_requirements import (
        STATE_KEY as MANIFEST_KEY,
    )
    from yleum_api.services.generation.recovery_requirements import frozen_requirements

    if template not in {"max_miniapp", "max-miniapp-nextjs"}:
        return None
    manifest = run.agent_state.get(MANIFEST_KEY)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("original_prompt"), str):
        return None
    mode = mode_for_prompt(manifest["original_prompt"])
    if mode is None or manifest.get("named") is not None or run.response_mode != mode:
        return None
    required = tuple(item for item in frozen_requirements(run) if item.required)
    if (
        len(required) != 1
        or required[0].check_kind != "manual"
        or required[0].text != manifest["original_prompt"]
    ):
        return None
    return MealContract(
        template,
        run.prompt_hash,
        manifest["manifest_sha256"],
        required[0].id,
        mode,
    )


def runner_digest() -> str:
    root = Path(__file__).resolve().parents[1]
    files = (
        "meal_acceptance_browser.py",
        "meal_acceptance_worker.py",
        "meal_pdf_content.py",
        "meal_acceptance_entitlement.py",
        "meal_acceptance_guardian.py",
        "max_behavior_browser_worker.py",
        "max_behavior_browser.py",
    )
    return digest(
        {
            name: hashlib.sha256((root / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for name in files
        }
    )
