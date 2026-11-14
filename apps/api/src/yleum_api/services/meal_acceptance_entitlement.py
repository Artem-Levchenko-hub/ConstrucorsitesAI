"""Exercise real candidate endpoints with purpose-bound synthetic checkout receipts."""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from yleum_api.services.generation.meal_acceptance import PACK_AMOUNT_KOPECKS, PACK_SKU, SHOP_CHECKS
from yleum_api.services.max_checkout_receipt import sign_checkout_receipt

_ACTOR = re.compile(r"mealqa_[0-9a-f]{32}")
_COOKIE = re.compile(r"[A-Za-z0-9_.-]{1,4096}")
_RECIPE = re.compile(r"[a-zA-Z0-9_-]{1,96}")
_MAX_RESPONSE = 262144


class MealEntitlementFailure(ValueError):
    def __init__(self, check: str) -> None:
        self.check = check
        super().__init__(check)


def _claims(project_id: UUID, buyer: str, order_id: UUID) -> dict[str, object]:
    now = int(time.time())
    return {
        "purpose": "yleum.max.checkout.receipt",
        "project_id": str(project_id),
        "actor_id": buyer,
        "order_id": str(order_id),
        "sku": PACK_SKU,
        "quantity": 1,
        "amount_kopecks": PACK_AMOUNT_KOPECKS,
        "currency": "RUB",
        "test": True,
        "iat": now,
        "exp": now + 300,
    }


def _request(
    client: httpx.Client, method: str, path: str, *,
    check: str | None = None, read_body: bool = True, **kwargs: Any,
) -> tuple[int, Any]:
    with client.stream(method, path, **kwargs) as response:
        status = response.status_code
        if status >= 500 or 300 <= status < 400:
            raise ValueError("meal_candidate_entitlement_unavailable")
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise ValueError("meal_candidate_entitlement_encoding_invalid")
        if status != 200 and check is not None:
            # No candidate-controlled text or data is accepted as a private denial.
            if any(response.iter_bytes()):
                raise MealEntitlementFailure(check)
        if status == 200 and read_body:
            try:
                return status, _json_body(response, require_json=check is not None)
            except ValueError:
                if check is not None:
                    raise MealEntitlementFailure(check) from None
                raise
        return status, None


def _json_body(response: httpx.Response, *, require_json: bool = True) -> Any:
    media_type = response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
    if require_json and media_type != "application/json":
        raise ValueError("meal_candidate_entitlement_invalid")
    body = bytearray()
    for chunk in response.iter_bytes(chunk_size=16384):
        body.extend(chunk)
        if len(body) > _MAX_RESPONSE:
            raise ValueError("meal_candidate_entitlement_body_limit")
    return json.loads(body)


def _recipes(value: object) -> dict[str, str]:
    if not isinstance(value, list) or len(value) != 10:
        raise MealEntitlementFailure("entitlement_ten_recipes")
    result: dict[str, str] = {}
    for row in value:
        if (
            type(row) is not dict
            or set(row) != {"id", "title"}
            or not isinstance(row["id"], str)
            or not _RECIPE.fullmatch(row["id"])
            or not isinstance(row["title"], str)
            or not row["title"].strip()
            or not 1 <= len(row["title"]) <= 160
            or row["id"] in result
        ):
            raise MealEntitlementFailure("entitlement_ten_recipes")
        result[row["id"]] = row["title"]
    if len({title.strip().casefold() for title in result.values()}) != len(result):
        raise MealEntitlementFailure("entitlement_ten_recipes")
    return result


def _prior_checkout(raw: object) -> tuple[UUID, list[str]]:
    if type(raw) is not dict or set(raw) != {"order_id", "recipe_ids"}:
        raise ValueError("meal_candidate_checkout_baseline_invalid")
    order, ids = raw["order_id"], raw["recipe_ids"]
    order_id = UUID(order) if type(order) is str else None
    if (
        str(order_id) != order or type(ids) is not list or len(ids) != 10
        or not all(type(value) is str and _RECIPE.fullmatch(value) for value in ids)
        or len(set(ids)) != 10
    ):
        raise ValueError("meal_candidate_checkout_baseline_invalid")
    assert order_id is not None
    return order_id, sorted(ids)


def _same_recipes(
    client: httpx.Client, path: str, expected: list[str] | dict[str, str],
) -> dict[str, str]:
    check = "entitlement_repeat"
    status, rows = _request(client, "GET", path, check=check)
    if status != 200:
        raise MealEntitlementFailure(check)
    try:
        current = _recipes(rows)
    except MealEntitlementFailure:
        raise MealEntitlementFailure(check) from None
    if (sorted(current) if isinstance(expected, list) else current) != expected:
        raise MealEntitlementFailure(check)
    return current


def observe_meal_entitlement(
    payload: dict[str, Any], *, capture: dict[str, Any] | None = None,
) -> dict[str, bool]:
    """The runner alone chooses the fixture. The candidate receives only signed data."""
    origin = payload["origin"]
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
        or parsed.password is not None or origin != f"https://{parsed.netloc}"
    ):
        raise ValueError("meal_candidate_origin_invalid")
    buyer, other = payload["buyer_id"], payload["other_id"]
    if not _ACTOR.fullmatch(buyer) or not _ACTOR.fullmatch(other) or buyer == other:
        raise ValueError("meal_candidate_actor_invalid")
    buyer_cookie, other_cookie = payload["buyer_cookie"], payload["other_cookie"]
    if (
        not _COOKIE.fullmatch(buyer_cookie) or not _COOKIE.fullmatch(other_cookie)
        or buyer_cookie == other_cookie
    ):
        raise ValueError("meal_candidate_actor_invalid")
    path = "/api/meal/recipes"
    grant_path = "/api/meal/entitlement"
    project_id = UUID(payload["project_id"])
    mode = payload["mode"]
    if mode not in {"build", "edit"}:
        raise ValueError("meal_candidate_mode_invalid")
    baseline = None
    if mode == "edit" or payload.get("prior_checkout") is not None:
        order_id, baseline = _prior_checkout(payload.get("prior_checkout"))
    else:
        order_id = UUID(payload["order_id"])
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(payload["fixture_seed"]))

    def connect(cookie: str) -> httpx.Client:
        return httpx.Client(
            base_url=origin,
            timeout=3,
            follow_redirects=False,
            trust_env=False,
            headers={
                "Cookie": f"__Host-max_session={cookie}",
                "Origin": origin,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Cache-Control": "no-cache",
            },
        )

    def denied(client: httpx.Client, uri: str, check: str) -> None:
        if _request(client, "GET", uri, check=check, read_body=False)[0] not in {401, 403, 404}:
            raise MealEntitlementFailure(check)

    def grant(client: httpx.Client, token: str, check: str) -> int:
        return _request(
            client, "POST", grant_path, check=check, read_body=False, json={"receipt": token},
        )[0]

    with connect(buyer_cookie) as first, connect(other_cookie) as second:
        for client, expected_actor in ((first, buyer), (second, other)):
            status, identity = _request(client, "GET", "/__omnia/identity")
            if (
                status != 200
                or type(identity) is not dict
                or identity.get("user_id") != expected_actor
                or identity.get("project_id") != str(project_id)
                or identity.get("epoch") != payload["fencing_epoch"]
            ):
                raise ValueError("meal_candidate_actor_unverified")
        if baseline is None:
            denied(first, path, "entitlement_denied_before_confirmation")
        else:
            _same_recipes(first, path, baseline)
        isolation = "entitlement_actor_isolation"
        invalid_receipts = "entitlement_invalid_receipts_denied"
        denied(second, path, isolation)
        claims = _claims(project_id, buyer, order_id)
        receipt = sign_checkout_receipt(claims, private_key=private_key)
        bad_claims = (
            {"sku": "foreign-pack"},
            {"amount_kopecks": PACK_AMOUNT_KOPECKS + 1},
            {"test": False},
            {"currency": "USD"},
            {"actor_id": other},
            {"project_id": str(UUID(int=project_id.int ^ 1))},
        )
        head, signed_payload, signature = receipt.split(".")
        changed = ("A" if signature[0] != "A" else "B") + signature[1:]
        invalid = (
            f"{head}.{signed_payload}.{changed}",
            *(
                sign_checkout_receipt({**claims, **patch}, private_key=private_key)
                for patch in bad_claims
            ),
        )
        for token in invalid:
            if grant(first, token, invalid_receipts) not in {400, 401, 403, 404, 409, 422}:
                raise MealEntitlementFailure(invalid_receipts)
        if baseline is None:
            denied(first, path, invalid_receipts)
        else:
            _same_recipes(first, path, baseline)
        if grant(second, receipt, isolation) not in {400, 401, 403, 404, 409, 422}:
            raise MealEntitlementFailure(isolation)
        recipe_check = "entitlement_ten_recipes"
        grant_check = "entitlement_repeat" if baseline is not None else recipe_check
        if grant(first, receipt, grant_check) != 200:
            raise MealEntitlementFailure(grant_check)
        if baseline is not None:
            recipes = _same_recipes(first, path, baseline)
        else:
            status, rows = _request(first, "GET", path, check=recipe_check)
            if status != 200:
                raise MealEntitlementFailure(recipe_check)
            recipes = _recipes(rows)
        for identifier, title in recipes.items():
            recipe_path = f"{path}/{identifier}"
            status, item = _request(first, "GET", recipe_path, check=recipe_check)
            if (
                status != 200
                or type(item) is not dict
                or set(item) != {"id", "title", "ingredients", "instructions"}
                or item["id"] != identifier
                or item["title"] != title
                or type(item["ingredients"]) is not list
                or not 1 <= len(item["ingredients"]) <= 64
                or not all(
                    type(value) is str and len(value) <= 512 and value.strip()
                    for value in item["ingredients"]
                )
                or type(item["instructions"]) is not str
                or len(item["instructions"]) > 10000
                or not item["instructions"].strip()
            ):
                raise MealEntitlementFailure(recipe_check)
            denied(second, recipe_path, isolation)
        denied(second, path, isolation)
        if grant(first, receipt, "entitlement_repeat") != 200:
            raise MealEntitlementFailure("entitlement_repeat")
        with connect(buyer_cookie) as fresh:
            _same_recipes(fresh, path, recipes)
        denied(second, path, isolation)
        for identifier in recipes:
            denied(second, f"{path}/{identifier}", isolation)
    if capture is not None:
        capture["checkout"] = {"order_id": str(order_id), "recipe_ids": sorted(recipes)}
    return dict.fromkeys(SHOP_CHECKS, True)
