"""Real tier/admin sessions cannot stream stored user attachments."""

from io import BytesIO
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from yleum_api.core.config import get_settings
from yleum_api.core.security import create_access_token
from yleum_api.models.billing import BillingAccount, BillingPlan, Subscription
from yleum_api.models.task_board import TaskBoardAttachment, TaskBoardTask
from yleum_api.models.user import User
from yleum_api.routers import task_board
from yleum_api.services.entitlements import load_plan_context


@pytest.mark.parametrize("persona", ["free", "pro", "business", "admin"])
async def test_tier_and_admin_attachment_downloads_deny_before_storage(
    client, db_session, test_engine, monkeypatch, persona
):
    owner = User(
        id=uuid4(), email=f"attachment-{uuid4().hex}@example.com",
        password_hash="fixture", role="admin" if persona == "admin" else "user",
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
    db_session.add(Subscription(
        billing_account_id=account.id, user_id=owner.id, plan_id=plan.id, status="active"
    ))
    task = TaskBoardTask(id=uuid4(), title="Own synthetic attachment", assignee="artem")
    db_session.add(task)
    await db_session.flush()
    attachment = TaskBoardAttachment(
        id=uuid4(), task_id=task.id, filename="fixture.html", content_type="text/html",
        size=24, object_key="task-board/fixture/" + uuid4().hex,
    )
    db_session.add(attachment)
    await db_session.commit()
    assert (await load_plan_context(db_session, owner.id)).plan.code == plan_code
    token = create_access_token(owner.id, session_version=owner.session_version)
    client.cookies.set(get_settings().jwt_cookie_name, token)
    assert (await client.get("/api/auth/me")).status_code == 200
    storage = Mock(return_value=BytesIO(b"synthetic-private-attachment"))
    monkeypatch.setattr(task_board, "_load_attachment", storage)
    sql = []

    def capture(_conn, _cursor, statement, *_args):
        sql.append(statement)

    event.listen(test_engine.sync_engine, "before_cursor_execute", capture)
    try:
        path = f"/api/task-board/tasks/{task.id}/attachments/{attachment.id}"
        for query in ("", f"?token={token}&signature=old-user-link"):
            response = await client.get(path + query)
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "forbidden"
            assert "synthetic-private-attachment" not in response.text
        storage.assert_not_called()
        assert not any("FROM task_board_attachments" in s for s in sql)
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", capture)
    listed = await client.get("/api/task-board/tasks")
    assert listed.status_code == 200
    assert listed.json()[0]["attachments"][0]["filename"] == "fixture.html"
    client.cookies.clear()
    assert (await client.get(path + "?token=" + token)).status_code == 403
    storage.assert_not_called()
