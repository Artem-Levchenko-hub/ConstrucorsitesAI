"""Separate installed notes executor using the existing owned Linux guardian."""

from typing import Any

from yleum_api.services import max_behavior_browser_worker as guardian
from yleum_api.services.notes_acceptance_browser import observe_notes


def _execute(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"status": "PASS", "outcomes": observe_notes(payload)}
    except BaseException:
        return {"status": "ERROR"}  # no generated data/credentials in diagnostics


if __name__ == "__main__":
    guardian._execute = _execute  # private process only; old worker is unchanged
    try:
        guardian.main()
    except BaseException:
        print('{"status":"ERROR","cleanup_closed":false}', flush=True)
        raise SystemExit(1) from None
