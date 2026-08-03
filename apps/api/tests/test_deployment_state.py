from omnia_api.services.deployment_state import deployment_is_active


def test_empty_queued_deploy_sentinel_is_not_active() -> None:
    assert not deployment_is_active({"phase": "queued", "run_id": None, "started_at": None})


def test_real_queued_and_building_deploys_are_active() -> None:
    assert deployment_is_active({"phase": "queued", "run_id": "run-1"})
    assert deployment_is_active({"phase": "building"})


def test_terminal_deploy_is_not_active() -> None:
    assert not deployment_is_active({"phase": "done", "run_id": "run-1"})
