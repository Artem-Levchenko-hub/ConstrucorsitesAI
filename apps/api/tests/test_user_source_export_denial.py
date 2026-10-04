"""Real user HTTP/PG: no plan/admin/source-query bypass and no storage access."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import event, select

from yleum_api.core.config import get_settings
from yleum_api.core.security import create_access_token
from yleum_api.models.billing import BillingAccount, BillingPlan, Subscription
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.models.user import User
from yleum_api.routers import backups
from yleum_api.services import repo
from yleum_api.services.entitlements import load_plan_context


@pytest.mark.parametrize("persona", ["free", "pro", "business", "admin"])
@pytest.mark.parametrize("operation", ["archive", "account", "source"])
async def test_authenticated_user_cannot_export_or_read_source(
    client, db_session, test_engine, monkeypatch, persona, operation
):
    owner = User(
        id=uuid4(),
        email=f"deny-{uuid4().hex}@example.com",
        password_hash="fixture",
        role="admin" if persona == "admin" else "user",
    )
    db_session.add(owner)
    await db_session.flush()
    account = BillingAccount(
        personal_user_id=owner.id, created_by_user_id=owner.id, scope="personal"
    )
    db_session.add(account)
    await db_session.flush()
    plan_code = "business" if persona == "admin" else persona
    plan = await db_session.scalar(
        select(BillingPlan).where(BillingPlan.code == plan_code, BillingPlan.is_active.is_(True))
    )
    db_session.add(
        Subscription(
            billing_account_id=account.id, user_id=owner.id, plan_id=plan.id, status="active"
        )
    )
    project = Project(
        id=uuid4(),
        owner_id=owner.id,
        name="Own source",
        slug="source-" + uuid4().hex,
        template="max_miniapp",
    )
    db_session.add(project)
    await db_session.flush()
    snapshot = Snapshot(id=uuid4(), project_id=project.id, commit_sha="a" * 40)
    db_session.add(snapshot)
    await db_session.flush()
    project.current_snapshot_id = snapshot.id
    await db_session.commit()
    assert (await load_plan_context(db_session, owner.id)).plan.code == plan_code
    token = create_access_token(owner.id, session_version=owner.session_version)
    client.cookies.set(get_settings().jwt_cookie_name, token)
    backup_settings = get_settings().model_copy(
        update={"offhost_backup_read_token": SecretStr("synthetic-machine-only-token")}
    )
    monkeypatch.setattr(backups, "get_settings", lambda: backup_settings)
    backup_storage = Mock(side_effect=AssertionError("user cannot read disaster archives"))
    monkeypatch.setattr(backups, "_latest_export", backup_storage)
    storage = Mock(return_value={"src/app/page.tsx": "synthetic-private-source"})
    monkeypatch.setattr(repo, "read_files", storage)
    sql = []

    def capture(_conn, _cursor, statement, *_args):
        sql.append(statement)

    event.listen(test_engine.sync_engine, "before_cursor_execute", capture)
    try:
        for query in ("", f"?token={token}&signature=old-user-link&include_files=true"):
            sql.clear()
            if operation == "archive":
                archive = await client.get(f"/api/projects/{project.id}/download{query}")
                assert archive.status_code == 403
                assert archive.json()["error"]["code"] == "forbidden"
                assert not any("FROM projects" in s or "FROM snapshots" in s for s in sql)
            elif operation == "account":
                sql.clear()
                exported = await client.get(f"/api/account/export{query}")
                assert exported.status_code == 403
                assert exported.json()["error"]["code"] == "forbidden"
                assert not any("FROM billing_accounts" in s or "FROM projects" in s for s in sql)
            else:
                detail = await client.get(
                    f"/api/projects/{project.id}/snapshots/{snapshot.id}{query}"
                )
                assert detail.status_code == 200
                payload = detail.json()
                assert (
                    payload["id"] == str(snapshot.id)
                    and payload["commit_sha"] == snapshot.commit_sha
                )
                assert "files" not in payload and "synthetic-private-source" not in detail.text
        storage.assert_not_called()
        for endpoint in ("status", "latest"):
            denied_backup = await client.get("/api/backups/offhost/" + endpoint)
            assert denied_backup.status_code == 403
            assert (
                await client.get(
                    "/api/backups/offhost/" + endpoint,
                    headers={"Authorization": "Bearer " + token},
                )
            ).status_code == 403
        backup_storage.assert_not_called()
        # The same authenticated owner still sees normal account/version metadata.
        assert (await client.get("/api/auth/me")).status_code == 200
        listed = await client.get(f"/api/projects/{project.id}/snapshots")
        assert listed.status_code == 200 and listed.json()[0]["id"] == str(snapshot.id)
        assert "files" not in listed.json()[0]
        foreign = await client.get(f"/api/projects/{uuid4()}/snapshots/{snapshot.id}")
        assert foreign.status_code == 404
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", capture)


async def test_old_user_query_link_does_not_authenticate(client, monkeypatch):
    storage = Mock(side_effect=AssertionError("source storage must not be accessed"))
    monkeypatch.setattr(repo, "read_files", storage)
    token = create_access_token(uuid4())
    for path in (
        f"/api/projects/{uuid4()}/download",
        "/api/account/export",
        f"/api/projects/{uuid4()}/snapshots/{uuid4()}",
    ):
        response = await client.get(path + f"?token={token}&signature=old-user-link")
        assert response.status_code == 401
    storage.assert_not_called()
