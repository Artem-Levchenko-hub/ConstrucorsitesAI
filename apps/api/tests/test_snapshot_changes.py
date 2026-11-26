import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from threading import BoundedSemaphore, Event
from uuid import uuid4

import pytest

from yleum_api.core.deps import get_current_user
from yleum_api.main import app
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.models.user import User
from yleum_api.services import max_owner_diagnosis, max_source_workers, repo


@dataclass(frozen=True)
class History:
    other_user: User
    project: Project
    other_project: Project
    parent: Snapshot
    version: Snapshot
    latest: Snapshot
    foreign_parent: Snapshot
    message: Message
    run: GenerationRun


@pytest.fixture
async def history(db_session):
    owner, other_user = User(), User()
    db_session.add_all([owner, other_user])
    await db_session.flush()
    project = Project(owner_id=owner.id, template="max_miniapp", name="Store", slug="store")
    other = Project(owner_id=other_user.id, template="max_miniapp", name="Other", slug="other")
    db_session.add_all([project, other])
    await db_session.flush()

    parent_sha = repo.init_from_files(
        project.id,
        {"src/app/page.tsx": "export default function Page() { return <h1>Магазин</h1> }"},
        "Initial",
    )
    first_sha = repo.commit_files(
        project.id,
        {
            "src/app/catalog/page.tsx": (
                "export default function Page() { return <h1>Каталог товаров</h1> }"
            ),
        },
        "Catalog",
        parent_sha,
    )
    latest_sha = repo.commit_files(
        project.id,
        {
            "src/app/news/page.tsx": (
                "export default function Page() { return <h1>Новости магазина</h1> }"
            ),
        },
        "News",
        first_sha,
    )
    parent = Snapshot(project_id=project.id, commit_sha=parent_sha)
    # A previously restored-to snapshot is still an eligible generation.
    first = Snapshot(project_id=project.id, commit_sha=first_sha, is_rollback_target=True)
    latest = Snapshot(project_id=project.id, commit_sha=latest_sha)
    # Even the correct SHA is ineligible as the parent when its row belongs elsewhere.
    foreign_parent = Snapshot(project_id=other.id, commit_sha=parent_sha)
    db_session.add_all([parent, first, latest, foreign_parent])
    await db_session.flush()

    first.parent_id = parent.id
    latest.parent_id = first.id
    project.current_snapshot_id = latest.id
    message = Message(
        project_id=project.id, snapshot_id=first.id, role="assistant", content="Готово",
    )
    db_session.add(message)
    await db_session.flush()
    run = GenerationRun(
        project_id=project.id, user_id=owner.id, assistant_message_id=message.id,
        status="completed", idempotency_key="catalog", prompt_hash="a" * 64,
    )
    db_session.add(run)
    await db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: owner
    return History(
        other_user, project, other, parent, first, latest, foreign_parent, message, run,
    )


def changes_url(project: Project, snapshot: Snapshot) -> str:
    return f"/api/projects/{project.id}/snapshots/{snapshot.id}/changes"


async def test_saved_version_reads_its_own_git_parent_once(
    client, db_session, history, monkeypatch,
):
    downloads = []
    download = repo._try_download

    def count_download(project_id, destination):
        downloads.append(project_id)
        return download(project_id, destination)

    monkeypatch.setattr(repo, "_try_download", count_download)
    chat = await client.get(f"/api/projects/{history.project.id}/messages")
    assert chat.status_code == 200
    assert downloads == []

    response = await client.get(changes_url(history.project, history.version))
    assert response.status_code == 200, response.text
    assert response.json() == {"changes": ["Добавили заголовок «Каталог товаров»."]}
    assert response.headers["Cache-Control"] == "private, no-store"
    assert downloads == [history.project.id]

    await db_session.refresh(history.project)
    await db_session.refresh(history.message)
    await db_session.refresh(history.run)
    assert history.project.current_snapshot_id == history.latest.id
    assert history.message.snapshot_id == history.version.id
    assert history.message.content == "Готово"
    assert history.run.status == "completed"


async def test_root_version_can_use_an_empty_baseline(client, db_session, history):
    history.message.snapshot_id = history.parent.id
    await db_session.commit()

    response = await client.get(changes_url(history.project, history.parent))
    assert response.status_code == 200, response.text
    assert response.json()["changes"][0] == "Создали первую версию приложения."


@pytest.mark.parametrize("case", [
    "other_owner", "other_snapshot", "other_parent", "non_max_project", "failed_run",
    "failed_retry_for_same_message", "other_project_run", "other_project_message",
    "user_message", "read_only_message", "rollback",
])
async def test_ineligible_history_is_denied_before_source_read(
    client, db_session, history, monkeypatch, case,
):
    snapshot = history.version
    if case == "other_owner":
        app.dependency_overrides[get_current_user] = lambda: history.other_user
    elif case == "other_snapshot":
        snapshot = history.foreign_parent
    elif case == "other_parent":
        history.version.parent_id = history.foreign_parent.id
    elif case == "non_max_project":
        history.project.template = "blank"
    elif case == "failed_run":
        history.run.status = "failed"
    elif case == "failed_retry_for_same_message":
        db_session.add(GenerationRun(
            project_id=history.project.id,
            user_id=history.project.owner_id,
            assistant_message_id=history.message.id,
            status="failed",
            idempotency_key="legacy-retry",
            prompt_hash="a" * 64,
            created_at=history.run.created_at + timedelta(seconds=1),
        ))
    elif case == "other_project_run":
        history.run.project_id = history.other_project.id
    elif case == "other_project_message":
        history.message.project_id = history.other_project.id
    elif case == "user_message":
        history.message.role = "user"
    elif case == "read_only_message":
        history.message.chat_question = True
    elif case == "rollback":
        history.latest.prompt_text = "Восстановление версии"
        snapshot = history.latest
    await db_session.commit()

    def unexpected_download(*_args):
        pytest.fail("Ineligible version must not open project source")

    monkeypatch.setattr(repo, "_try_download", unexpected_download)
    response = await client.get(changes_url(history.project, snapshot))
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("case", ["disconnected", "wrong_parent", "missing_commit"])
async def test_unverifiable_git_history_has_no_substitute(client, db_session, history, case):
    if case == "disconnected":
        history.version.parent_id = None
    elif case == "wrong_parent":
        history.version.parent_id = history.latest.id
    elif case == "missing_commit":
        history.version.commit_sha = uuid4().hex + "0" * 8
    await db_session.commit()

    response = await client.get(changes_url(history.project, history.version))
    assert response.status_code == 404, response.text
    assert response.json() == {
        "error": {"code": "not_found", "message": "Отчёт о версии недоступен."},
    }


async def test_history_storage_failure_returns_a_neutral_error(client, history, monkeypatch):
    def unavailable(*_args):
        raise OSError("internal-storage-name")

    monkeypatch.setattr(repo, "_try_download", unavailable)
    response = await client.get(changes_url(history.project, history.version))
    assert response.status_code == 503, response.text
    assert response.json() == {
        "error": {"code": "unavailable", "message": "Не удалось загрузить отчёт о версии."},
    }


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
async def test_history_retains_its_own_slot_and_leaves_a_reader_for_diagnosis(
    client, db_session, history, monkeypatch, stop,
):
    source = {"src/app/page.tsx": "export default () => <button>Добавить товар</button>;"}
    diagnosis_sha = repo.init_from_files(history.other_project.id, source, "Another app")
    history.foreign_parent.commit_sha = diagnosis_sha
    await db_session.commit()

    pool = ThreadPoolExecutor(max_workers=2)
    source_slots, history_slots = BoundedSemaphore(2), BoundedSemaphore(1)
    monkeypatch.setattr(max_source_workers, "_SOURCE_EXECUTOR", pool)
    monkeypatch.setattr(max_source_workers, "_SOURCE_SLOTS", source_slots)
    monkeypatch.setattr(max_source_workers, "_HISTORY_SLOTS", history_slots)
    started, finish = Event(), Event()
    archive_reads = []
    project_id = history.project.id
    download = repo._try_download

    def delay_first_archive_read(requested_project, destination):
        if requested_project == project_id:
            archive_reads.append(requested_project)
            if len(archive_reads) == 1:
                started.set()
                assert finish.wait(15)
        return download(requested_project, destination)

    monkeypatch.setattr(repo, "_try_download", delay_first_archive_read)
    url = changes_url(history.project, history.version)
    first = asyncio.create_task(client.get(url))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        duplicate = await client.get(url)
        assert duplicate.status_code == 503, duplicate.text

        read_sha, controls = await max_owner_diagnosis._source_facts(
            db_session, history.other_project.id, history.foreign_parent.id, "Добавить товар",
        )
        assert read_sha == diagnosis_sha
        assert [control["label"] for control in controls] == ["Добавить товар"]

        if stop == "cancel":
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            assert (await first).status_code == 503
        after_waiter = await client.get(url)
        assert after_waiter.status_code == 503, after_waiter.text
        assert archive_reads == [project_id]
    finally:
        finish.set()
        await asyncio.gather(first, return_exceptions=True)
        await asyncio.to_thread(pool.shutdown)
    assert history_slots.acquire(blocking=False)
    assert source_slots.acquire(blocking=False)
    assert source_slots.acquire(blocking=False)
