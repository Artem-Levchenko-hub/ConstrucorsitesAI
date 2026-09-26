"""Task10B routing baseline: actual callers; no screenshot/runtime/model acceptance."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from yleum_api.core.errors import ApiError
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.routers import rollback
from yleum_api.services.generation import lifecycle

BROWSER_CONTAINERS = ("fullstack", "nextjs_entities", "spa", "realtime", "max_miniapp")
NON_BROWSER = ("blank", "landing", "portfolio", "blog", "api", "tgbot", "code")


def test_current_ordered_browser_facets_are_exact():
    # The runtime router no longer needs the family: it only talks to cells.
    assert lifecycle.CONTAINER_NEXT == BROWSER_CONTAINERS
    assert rollback._CONTAINER_NEXT == BROWSER_CONTAINERS


class Session:
    def __init__(self, project, snapshot):
        self.project, self.snapshot = project, snapshot

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, model, identity):
        if model is Project and identity == self.project.id:
            return self.project
        if model is Snapshot and identity == self.snapshot.id:
            return self.snapshot
        return None


def rows(template):
    owner, project_id, snapshot_id = uuid4(), uuid4(), uuid4()
    project = SimpleNamespace(
        id=project_id,
        owner_id=owner,
        template=template,
        slug="fixture",
        current_snapshot_id=snapshot_id,
    )
    snapshot = SimpleNamespace(id=snapshot_id, project_id=project_id, commit_sha="a" * 40)
    return project, snapshot, SimpleNamespace(id=owner)


class ReachedNavigation(BaseException):
    """Stop before actual browser/network/PNG/MinIO; prove selected navigation only."""


@pytest.mark.parametrize("template", [*BROWSER_CONTAINERS, *NON_BROWSER])
def test_rollback_actual_handler_browser_and_negative_facets(template, monkeypatch):
    from tests.test_rollback_container_hot_reload import (
        _make_project,
        _patch_common,
        _run_rollback,
    )

    hot_calls = []
    _patch_common(monkeypatch, hot_calls)
    project = _make_project(template)
    if template == "max_miniapp":
        old = project.current_snapshot_id
        checkout = Mock(side_effect=AssertionError("MAX must reject before Git"))
        monkeypatch.setattr(rollback.repo_svc, "checkout", checkout)
        with pytest.raises(ApiError) as error:
            _run_rollback(project)
        assert error.value.status_code == 409
        assert project.current_snapshot_id == old and hot_calls == []
        checkout.assert_not_called()
    else:
        session, _result = _run_rollback(project)
        assert session.committed
        assert len(hot_calls) == (1 if template in BROWSER_CONTAINERS else 0)
