"""Run the Meal entitlement observer against a stateful, signed fixture transport."""

import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from yleum_api.services import meal_acceptance_entitlement as entitlement

PROJECT = "11111111-1111-4111-8111-111111111111"
ORDER = "22222222-2222-4222-8222-222222222222"
NEW_ORDER = "33333333-3333-4333-8333-333333333333"
BUYER = "mealqa_" + "a" * 32
OTHER = "mealqa_" + "b" * 32
ORIGIN = "https://meal-candidate.example"
RECIPES = [{"id": f"recipe-{i}", "title": f"Блюдо {i}"} for i in range(10)]
BASELINE = {"order_id": ORDER, "recipe_ids": sorted(row["id"] for row in RECIPES)}
FIXTURE_SEED = b"a" * 32


@pytest.fixture
def candidate(monkeypatch):
    public_key = Ed25519PrivateKey.from_private_bytes(FIXTURE_SEED).public_key()
    state = SimpleNamespace(
        orders=set(), rows=RECIPES, requests=[], accepted=[], leak=None, redirect=False,
        invalid_detail=False, leak_detail_after_replay=False,
    )
    payload = {
        "origin": ORIGIN,
        "project_id": PROJECT,
        "fencing_epoch": 7,
        "mode": "build",
        "buyer_id": BUYER,
        "other_id": OTHER,
        "buyer_cookie": "buyer-session",
        "other_cookie": "other-session",
        "order_id": ORDER,
        "fixture_seed": FIXTURE_SEED.hex(),
    }

    def handler(request):
        assert f"{request.url.scheme}://{request.url.host}" == ORIGIN
        assert request.headers["origin"] == ORIGIN
        actor = {
            "__Host-max_session=buyer-session": BUYER,
            "__Host-max_session=other-session": OTHER,
        }[request.headers["cookie"]]
        state.requests.append((actor, request.method, request.url.path))
        path = request.url.path
        if state.redirect:
            return httpx.Response(308, headers={"Location": "https://foreign.example/"})
        if path == "/__omnia/identity":
            return httpx.Response(200, json={
                "project_id": PROJECT, "user_id": actor, "epoch": 7,
            })
        if path == "/api/meal/entitlement":
            token = json.loads(request.content)["receipt"]
            version, encoded, signature = token.split(".")
            assert version == "v1"
            try:
                public_key.verify(
                    base64.urlsafe_b64decode(signature + "=="), f"v1.{encoded}".encode(),
                )
            except InvalidSignature:
                return httpx.Response(403)
            claims = json.loads(base64.urlsafe_b64decode(encoded + "==="))
            required = {
                "purpose": "yleum.max.checkout.receipt", "actor_id": actor, "project_id": PROJECT,
                "sku": entitlement.PACK_SKU, "quantity": 1, "test": True, "currency": "RUB",
                "amount_kopecks": entitlement.PACK_AMOUNT_KOPECKS,
            }
            if any(claims[key] != value for key, value in required.items()):
                return httpx.Response(403)
            state.accepted.append(claims)
            state.orders.add((actor, claims["order_id"]))
            return httpx.Response(200, json={})
        if not any(owner == actor for owner, _ in state.orders):
            if (
                actor == OTHER and state.leak_detail_after_replay
                and len(state.accepted) == 2 and path.startswith("/api/meal/recipes/")
            ):
                return httpx.Response(403, json={"error": "private recipe detail"})
            if actor == OTHER and state.leak == "json":
                return httpx.Response(403, json={"message": "private recipe text"})
            if actor == OTHER and state.leak == "html":
                return httpx.Response(403, text="<p>private recipe text</p>")
            return httpx.Response(403)
        if path == "/api/meal/recipes":
            return httpx.Response(200, json=state.rows)
        row = next(row for row in state.rows if row["id"] == path.rsplit("/", 1)[1])
        extra = {"foreign_field": "private data"} if state.invalid_detail else {}
        return httpx.Response(200, json={
            **row, "ingredients": ["Крупа"], "instructions": "Сварить.", **extra,
        })

    client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(entitlement.httpx, "Client", lambda **kwargs: client(
        **kwargs, transport=transport,
    ))
    return state, payload


@pytest.mark.parametrize("mode", ["edit", "build"])
def test_build_captures_and_rebind_replays_exact_existing_order(candidate, monkeypatch, mode):
    state, payload = candidate
    monkeypatch.setattr(entitlement.time, "time", lambda: 1800)
    built = {}
    assert all(entitlement.observe_meal_entitlement(payload, capture=built).values())
    assert built == {"checkout": BASELINE}
    assert state.orders == {(BUYER, ORDER)}
    assert [row["iat"] for row in state.accepted] == [1800, 1800]

    state.requests.clear()
    state.accepted.clear()
    state.rows = list(reversed(RECIPES))
    monkeypatch.setattr(entitlement.time, "time", lambda: 2500)
    edited = {}
    rebind = {**payload, "mode": mode, "order_id": NEW_ORDER, "prior_checkout": built["checkout"]}
    assert all(entitlement.observe_meal_entitlement(rebind, capture=edited).values())
    assert edited == built
    first_post = next(i for i, (_, method, _) in enumerate(state.requests) if method == "POST")
    assert (BUYER, "GET", "/api/meal/recipes") in state.requests[:first_post]
    assert state.orders == {(BUYER, ORDER)}
    assert [(row["order_id"], row["iat"]) for row in state.accepted] == [(ORDER, 2500)] * 2


@pytest.mark.parametrize("changed", [False, True])
def test_edit_refuses_drop_or_change_before_post(candidate, changed):
    state, payload = candidate
    if changed:
        state.orders.add((BUYER, ORDER))
        state.rows = [*RECIPES[:-1], {"id": "other-dish", "title": "Другое блюдо"}]
    capture = {}
    with pytest.raises(entitlement.MealEntitlementFailure, match="entitlement_repeat"):
        entitlement.observe_meal_entitlement(
            {**payload, "mode": "edit", "prior_checkout": BASELINE}, capture=capture,
        )
    assert not capture and not state.accepted
    assert all(method != "POST" for _, method, _ in state.requests)


@pytest.mark.parametrize("fault", ["json", "html"])
def test_actor_denial_cannot_contain_recipe_data(candidate, fault):
    state, payload = candidate
    state.leak = fault
    with pytest.raises(entitlement.MealEntitlementFailure, match="entitlement_actor_isolation"):
        entitlement.observe_meal_entitlement(payload)
    assert not state.accepted


def test_candidate_redirect_never_receives_actor_cookie_off_origin(candidate):
    state, payload = candidate
    state.redirect = True
    with pytest.raises(ValueError, match="meal_candidate_entitlement_unavailable"):
        entitlement.observe_meal_entitlement(payload)
    assert state.requests == [(BUYER, "GET", "/__omnia/identity")]


@pytest.mark.parametrize("origin", [
    "http://meal-candidate.example",
    "https://foreign.example@meal-candidate.example",
    "https://meal-candidate.example/?redirect=https://foreign.example",
])
def test_candidate_origin_must_be_direct_https(candidate, origin):
    state, payload = candidate
    with pytest.raises(ValueError, match="meal_candidate_origin_invalid"):
        entitlement.observe_meal_entitlement({**payload, "origin": origin})
    assert not state.requests


@pytest.mark.parametrize("prior", [None, {**BASELINE, "recipe_ids": ["same"] * 10}])
def test_missing_or_invalid_edit_baseline_stops_before_connect(candidate, prior):
    state, payload = candidate
    with pytest.raises(ValueError, match="meal_candidate_checkout_baseline_invalid"):
        entitlement.observe_meal_entitlement({**payload, "mode": "edit", "prior_checkout": prior})
    assert not state.requests


def test_recipe_detail_cannot_include_extra_fields(candidate):
    state, payload = candidate
    state.invalid_detail = True
    with pytest.raises(entitlement.MealEntitlementFailure, match="entitlement_ten_recipes"):
        entitlement.observe_meal_entitlement(payload)


def test_other_actor_cannot_read_recipe_after_buyer_replays(candidate):
    state, payload = candidate
    state.leak_detail_after_replay = True
    capture = {}
    with pytest.raises(entitlement.MealEntitlementFailure, match="entitlement_actor_isolation"):
        entitlement.observe_meal_entitlement(payload, capture=capture)
    assert len(state.accepted) == 2 and not capture
