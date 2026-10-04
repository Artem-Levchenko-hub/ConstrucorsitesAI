"""Execute refreshed core routes and their real signed-actor forwarding hook."""

import json
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from tests.test_app_integrations_api import _max_init_data
from tests.test_integration_runtime_contracts import connect
from tests.test_max_project_kit import _config
from yleum_api.services.max_project_kit import render_max_managed_files

HARNESS = (Path(__file__).parent / "fixtures/max_analytics_core_harness.cjs").read_text()


def run(tmp_path, route, body, actor="42", public=True, **extra):
    project = extra.pop("project", uuid4())
    # An old customer product has no analytics helper before the atomic refresh.
    product = tmp_path / "src/app/page.tsx"
    product.parent.mkdir(parents=True, exist_ok=True)
    product.write_text("export default function Coffee(){return 'Coffee';}")
    files = render_max_managed_files(_config(), project)
    for path, content in files.items():
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
    result = subprocess.run(
        ["node", "-e", HARNESS],
        input=json.dumps(
            {
                "project": str(project),
                "tree": str(tmp_path / "src"),
                "route": route,
                "body": body,
                "actor": actor,
                "public": public,
                **extra,
            }
        ),
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    assert "private credentials" not in result.stderr
    assert product.read_text().endswith("'Coffee';}")
    return json.loads(result.stdout)


async def test_real_core_login_event_reaches_owner_aggregate(
    tmp_path, client, db_session, monkeypatch
):
    project = await connect(client, db_session, monkeypatch)
    result = run(
        tmp_path,
        "max/session",
        {"initData": _max_init_data("test-max-token", 42)},
        project=UUID(project),
    )
    assert result["status"] == 200
    assert result["trace"].index("persisted:maxAnalyticsEvents") < result["trace"].index("forward")
    assert result["trace"].index("session-cookie") < result["trace"].index("forward")
    sent = result["sent"][0]
    for _ in range(2):
        accepted = await client.post(
            f"/api/runtime/projects/{project}/analytics",
            content=sent["body"],
            headers=sent["headers"],
        )
        assert accepted.status_code == 204
    data = (await client.get(f"/api/projects/{project}/max/analytics")).json()
    assert data["users"] == 1 and data["opens"] == 1 and data["events"] == 1


@pytest.mark.parametrize(
    "route,body",
    [
        (
            "omnia/actions",
            {
                "actionType": "booking",
                "payload": {"email": "private@test"},
                "operationKey": "22222222-2222-4222-8222-222222222222",
            },
        ),
        (
            "omnia/events",
            {
                "eventName": "checkout",
                "properties": {"email": "private@test"},
                "eventId": "22222222-2222-4222-8222-222222222222",
            },
        ),
    ],
)
def test_durable_action_and_event_hook_omit_payloads(tmp_path, route, body):
    result = run(tmp_path, route, body)
    assert result.get("status") in {201, 204}, result
    assert result["trace"].index("forward") > 0
    assert len(result["sent"]) == 1
    assert "private@test" not in result["sent"][0]["body"]
    failure = run(tmp_path, route, body, upstreamFailure=True)
    assert failure["status"] == result["status"]
    assert len(failure["sent"]) == 2
    assert failure["sent"][0]["body"] == failure["sent"][1]["body"]
    assert run(tmp_path, route, body, dbFailure=True)["sent"] == []
    assert run(tmp_path, route, body, actor=None)["status"] == 401
    assert run(tmp_path, route, body, corrupt=True)["status"] == 401
    assert run(tmp_path, route, body, actor="preview", public=False)["sent"] == []
    assert run(tmp_path, route, body, public=False)["sent"] == []
    health = {**body, "actionType": "omnia_health_probe", "eventName": "omnia_health_probe"}
    assert run(tmp_path, route, health)["sent"] == []


def test_rejected_launch_and_failed_db_do_not_record_opens(tmp_path):
    assert run(tmp_path, "max/session", {"initData": "forged"})["sent"] == []
    result = run(
        tmp_path, "max/session", {"initData": _max_init_data("test-max-token", 42)}, dbFailure=True
    )
    assert result["status"] == 503 and result["sent"] == []
