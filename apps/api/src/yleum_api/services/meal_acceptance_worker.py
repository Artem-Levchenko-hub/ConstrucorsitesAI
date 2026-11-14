"""Linux guardian entry point for the installed candidate-owned Meal probe."""

from typing import Any

from yleum_api.services import max_behavior_browser_worker as guardian
from yleum_api.services.generation.meal_acceptance import SHOP_CHECKS, runner_digest
from yleum_api.services.meal_acceptance_browser import (
    BUILD_CHECKS,
    CHECKOUT_WIRING,
    EDIT_CHECKS,
    MealObservationFailed,
    observe_meal,
)
from yleum_api.services.meal_acceptance_entitlement import (
    MealEntitlementFailure,
    observe_meal_entitlement,
)


def _execute(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        if payload["runner_sha256"] != runner_digest():
            return {"status": "ERROR"}
        fixture: dict[str, Any] = {}
        entitlement: dict[str, bool] = {}

        def verify_entitlement() -> None:
            entitlement.update(observe_meal_entitlement(payload, capture=fixture))

        outcomes = observe_meal(payload, capture=fixture, on_ready=verify_entitlement)
        return {"status": "PASS", "outcomes": {**outcomes, **entitlement}, "fixture": fixture}
    except (MealObservationFailed, MealEntitlementFailure) as error:
        if error.check in {*BUILD_CHECKS, *EDIT_CHECKS, *SHOP_CHECKS, CHECKOUT_WIRING}:
            return {"status": "RED", "check": error.check}
        return {"status": "ERROR"}
    except BaseException:
        return {"status": "ERROR"}


if __name__ == "__main__":
    guardian._execute = _execute
    try:
        guardian.main()
    except BaseException:
        print('{"status":"ERROR","cleanup_closed":false}', flush=True)
        raise SystemExit(1) from None
