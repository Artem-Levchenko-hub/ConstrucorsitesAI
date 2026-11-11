"""Private unfinished source is not an accepted snapshot or a public export."""
import json
from uuid import UUID

import pytest

from tests.test_agent_retry_context import history  # noqa: F401 - real SQLite history fixture

PROJECT = UUID('11111111-1111-4111-8111-111111111111')
OWNER = UUID('22222222-2222-4222-8222-222222222222')
RUN = UUID('33333333-3333-4333-8333-333333333333')
BASE = 'a' * 40


@pytest.fixture
def checkpoints(history):  # noqa: F811 - pytest consumes imported fixture
    from uuid import uuid4

    from yleum_api.services.generation import source_checkpoint as sc
    from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, model, key, **kwargs):
            return history.session.get(model, key, **kwargs)

        async def scalar(self, statement):
            return history.session.scalar(statement)

        async def commit(self):
            history.session.commit()

    def ids(run):
        return GenerationIds(run.id, run.project_id, run.user_id,
                             run.user_message_id, uuid4())

    async def save(run):
        await sc.persist_checkpoint(
            factory=Session, ids=ids(run),
            baseline=SourceBaseline(None, BASE, {'src/app/old.ts': 'accepted old source'}),
            files={'src/app/page.tsx': 'saved feature', 'src/app/empty.ts': ''},
            original_prompt='Original product with orders',
        )
        run.status = 'failed'
        history.session.commit()

    async def resume(run, baseline_sha=BASE):
        return await sc.adjacent_checkpoint(
            factory=Session, ids=ids(run),
            baseline=SourceBaseline(None, baseline_sha, {}),
        )

    return save, resume


def _mark_failed(history_fixture, run):
    run.status = 'failed'
    history_fixture.session.commit()
    history_fixture.session.expire_all()


async def test_repeated_resume_failures_before_private_copy_keep_exact_saved_source(
    history, checkpoints,  # noqa: F811 - pytest consumes imported fixture
):
    save, resume = checkpoints
    original = history.add('Original product with orders', status='running')
    await save(original)
    expected = ({'files': {'src/app/page.tsx': 'saved feature', 'src/app/empty.ts': ''},
                 'deleted_paths': ['src/app/old.ts']}, 'Original product with orders')

    for request in ('продолжи', 'попробуй ещё раз', 'continue building'):
        current = history.add(request, status='running')
        assert await resume(current) == expected
        # The process fails before source is copied to the current run's private object.
        _mark_failed(history, current)


@pytest.fixture
async def first_build_checkpoint(history, monkeypatch):  # noqa: F811 - pytest consumes fixture
    from dataclasses import replace
    from datetime import UTC, timedelta
    from types import SimpleNamespace
    from unittest.mock import Mock
    from uuid import uuid4

    from yleum_api.services.generation import source_checkpoint as sc
    from yleum_api.services.generation.contracts import SourceBaseline
    from yleum_api.services.generation_runs import (
        load_generation_dispatch,
        store_generation_dispatch,
        write_capacity_dispatch_claim,
    )

    project = history.ensure_project()
    project.current_snapshot_id = None
    history.session.commit()
    baseline = SourceBaseline(None, None, {})

    def add(prompt, *, status='running', **kwargs):
        run = history.add(prompt, status=status, **kwargs)
        store_generation_dispatch(
            run, replace(load_generation_dispatch(run), current_snapshot_id=None),
        )
        if status == 'running':
            run.started_at = run.created_at
        history.session.commit()
        return run

    def finish(run, status):
        run.status = status
        run.finished_at = run.created_at + timedelta(milliseconds=500)
        history.session.commit()

    def cancelled(prompt='Продолжи сборку', *, worker_started=True, **kwargs):
        run = add(prompt, status='cancelled', **kwargs)
        run.execution_backend = 'worker'
        if worker_started:
            run.execution_started_at = run.created_at
        write_capacity_dispatch_claim(
            run, token=uuid4(), lease_seconds=60, now=run.created_at.replace(tzinfo=UTC),
        )
        finish(run, 'cancelled')
        return run

    async def resume(run):
        return await sc.adjacent_checkpoint(
            factory=history.factory, ids=history.ids(run), baseline=baseline,
        )

    prompt = 'Собери приложение для учёта КБЖУ'
    files = {'src/app/page.tsx': 'export default () => "КБЖУ";\n', 'src/app/empty.ts': ''}
    original = add(prompt)
    await sc.persist_checkpoint(
        factory=history.factory, ids=history.ids(original), baseline=baseline,
        files=files, original_prompt=prompt,
    )
    finish(original, 'failed')
    reads = Mock(wraps=sc._get_checkpoint)
    monkeypatch.setattr(sc, '_get_checkpoint', reads)
    return SimpleNamespace(
        project=project, original=original, add=add, cancelled=cancelled, finish=finish,
        resume=resume, reads=reads, expected=({'files': files, 'deleted_paths': []}, prompt),
    )


@pytest.mark.parametrize('worker_started', [False, True])
async def test_first_build_source_survives_pre_admission_cancellation_and_failed_retry(
    first_build_checkpoint, worker_started,
):
    first = first_build_checkpoint
    cancelled = first.cancelled(worker_started=worker_started)
    cancelled_state = dict(cancelled.agent_state)
    digest = first.original.agent_state['source_checkpoint']['digest']
    previous = first.original

    for _ in range(2):
        current = first.add('Продолжи')
        assert await first.resume(current) == first.expected
        assert current.agent_state['source_checkpoint_resume'] == {
            'previous_run_id': str(previous.id), 'source_run_id': str(first.original.id),
            'digest': digest, 'baseline_sha': None,
        }
        assert 'source_checkpoint' not in current.agent_state
        first.finish(current, 'failed')
        previous = current

    assert first.reads.call_count == 2
    assert cancelled.agent_state == cancelled_state
    assert first.project.current_snapshot_id is None


@pytest.mark.parametrize('intervening', [
    'started_at', 'admitted_token', 'amended', 'foreign_owner', 'foreign_dispatch',
    'dispatch_snapshot', 'attachment', 'selected_elements', 'malformed_font',
    'missing_claim', 'broken_claim', 'overflowing_claim',
    'assistant_snapshot', 'unfinished', 'finished_too_late',
])
async def test_first_build_source_does_not_cross_other_cancelled_attempts(
    history, first_build_checkpoint, intervening,  # noqa: F811 - pytest consumes fixture
):
    from uuid import uuid4

    from yleum_api.models.message import Message

    first = first_build_checkpoint
    prompt = 'Продолжи, но поменяй формулу' if intervening == 'amended' else 'Продолжи сборку'
    references = [{'id': str(uuid4()), 'sha256': 'a' * 64}] if intervening == 'attachment' else None
    cancelled = first.cancelled(
        prompt, user=uuid4() if intervening == 'foreign_owner' else None,
        references=references,
    )
    current = first.add('Продолжи')
    state = dict(cancelled.agent_state)
    dispatch = dict(state['dispatch'])
    if intervening == 'started_at':
        cancelled.started_at = cancelled.created_at
    elif intervening == 'admitted_token':
        state['capacity_admitted_dispatch_token'] = str(uuid4())
    elif intervening == 'foreign_dispatch':
        dispatch['user_id'] = str(uuid4())
    elif intervening == 'dispatch_snapshot':
        dispatch['current_snapshot_id'] = str(history.snapshot.id)
    elif intervening == 'selected_elements':
        dispatch['selected_elements'] = [{'path': 'src/app/page.tsx'}]
    elif intervening == 'malformed_font':
        dispatch['font_selections'] = [{'id': None, 'sha256': 'a' * 64, 'role': 'body'}]
    elif intervening == 'missing_claim':
        del state['capacity_dispatch_claim']
    elif intervening == 'broken_claim':
        state['capacity_dispatch_claim'] = {**state['capacity_dispatch_claim'], 'expires_at': 'bad'}
    elif intervening == 'overflowing_claim':
        state['capacity_dispatch_claim'] = {
            **state['capacity_dispatch_claim'], 'expires_at': '9999-12-31T23:59:59-01:00',
        }
    elif intervening == 'assistant_snapshot':
        assistant = history.session.get(Message, cancelled.assistant_message_id)
        assistant.snapshot_id = history.snapshot.id
    elif intervening == 'unfinished':
        cancelled.finished_at = None
    elif intervening == 'finished_too_late':
        cancelled.finished_at = current.created_at
    cancelled.agent_state = {**state, 'dispatch': dispatch}
    history.session.commit()

    assert await first.resume(current) is None
    first.reads.assert_not_called()
    assert 'source_checkpoint_resume' not in current.agent_state


async def test_first_build_source_rechecks_cancelled_predecessor_after_failed_retry(
    history, first_build_checkpoint,  # noqa: F811 - pytest consumes fixture
):
    first = first_build_checkpoint
    cancelled = first.cancelled()
    resumed = first.add('Продолжи')
    assert await first.resume(resumed) == first.expected
    first.finish(resumed, 'failed')
    cancelled.started_at = cancelled.created_at
    history.session.commit()

    retry = first.add('Продолжи')
    assert await first.resume(retry) is None
    assert first.reads.call_count == 1
    assert 'source_checkpoint_resume' not in retry.agent_state


@pytest.fixture
def reconciliation_operations(history):  # noqa: F811
    from datetime import timedelta
    from uuid import uuid4

    from sqlalchemy import JSON, Column, MetaData, Table
    from sqlalchemy.dialects.postgresql import JSONB

    from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
    from yleum_api.services.orchestrator_client import (
        ProjectCellLeaseObservation,
        ProjectCellResourceResponse,
    )
    from yleum_api.services.project_cells import _canonical_operation_envelope

    metadata = MetaData()
    for model in (ProjectCellOperation, ProjectCellWorkspace):
        Table(model.__tablename__, metadata, *(Column(
            column.name, JSON() if isinstance(column.type, JSONB) else column.type,
            primary_key=column.primary_key,
        ) for column in model.__table__.columns))
    metadata.create_all(history.session.get_bind())

    def for_project(project):
        workspace = ProjectCellWorkspace(
            id=uuid4(), project_id=project.id, owner_id=project.owner_id,
            provider='fixture', state='ready', fencing_epoch=10,
        )
        history.session.add(workspace)
        history.session.commit()
        return workspace

    def operation(workspace, *, generation_run_id, execution_run=None, kind='ensure', fence=7):
        request, digest = _canonical_operation_envelope(workspace.id, generation_run_id, kind, {})
        operation_id = uuid4()
        key = str(uuid4())
        result = None
        if kind == 'status':
            key = f'capacity:lease-observe:{workspace.id}:{generation_run_id}:{fence - 1}'
            lease = ProjectCellLeaseObservation(
                version=1, workspace_id=workspace.id, project_id=workspace.project_id,
                owner_id=workspace.owner_id, operation_id=operation_id, request_digest=digest,
                fencing_epoch=fence, active_generation_run_id=None,
                active_generation_fencing_epoch=None, quiescent=False,
            )
            result = ProjectCellResourceResponse(
                workspace_id=workspace.id, state='resources_ready', provider_ref='fixture',
                fencing_epoch=fence, checkpoint_ref=None, has_workspace=True,
                has_agent_home=True, has_postgres=True, has_redis=True, lease_observation=lease,
            ).to_wire_json()
        row = ProjectCellOperation(
            id=operation_id, workspace_id=workspace.id, generation_run_id=generation_run_id,
            execution_run_id=execution_run.id if execution_run else None,
            kind=kind, status='completed', fencing_epoch=fence,
            idempotency_key=key, request_digest=digest,
            request_payload=request, result_payload=result,
        )
        if execution_run is not None:
            started = execution_run.created_at + timedelta(milliseconds=10 * fence)
            row.created_at = started
            row.started_at = started
            row.finished_at = started + timedelta(milliseconds=5)
        history.session.add(row)
        history.session.commit()
        return row

    def observations(workspace, observed, execution_run):
        return [operation(workspace, generation_run_id=observed.id, execution_run=execution_run,
                          kind='status', fence=fence) for fence in range(7, 11)]

    return for_project, operation, observations


def _mark_reconciliation_failure(history_fixture, run):
    from datetime import timedelta

    from yleum_api.schemas.message import PromptResponse

    run.execution_backend = 'worker'
    run.execution_started_at = run.created_at
    run.error = 'Previous Project Cell ensure needs reconciliation'
    run.agent_state = {**run.agent_state, 'generation_billing': {'outcome': 'waived'},
                       'recovery_requirements_v1': {'version': 1}}
    run.response_payload = PromptResponse(
        run_id=run.id, message_id=run.assistant_message_id,
    ).model_dump(mode='json')
    run.finished_at = run.created_at + timedelta(milliseconds=500)
    history_fixture.session.commit()
    return run


@pytest.fixture
def reconciliation_failure_history(
    history, first_build_checkpoint, reconciliation_operations,  # noqa: F811
):
    from functools import partial
    from types import SimpleNamespace

    for_project, operation, observations = reconciliation_operations
    first = first_build_checkpoint
    cancelled = first.cancelled()
    workspace = for_project(first.project)
    operation(workspace, generation_run_id=cancelled.id)

    def fail(prompt='Продолжи сборку', **kwargs):
        run = first.add(prompt, status='failed', **kwargs)
        return _mark_reconciliation_failure(history, run)

    return SimpleNamespace(
        first=first, cancelled=cancelled, fail=fail, operation=partial(operation, workspace),
        observations=partial(observations, workspace, cancelled),
    )


async def test_first_build_source_survives_cancelled_and_reconciliation_failed_attempts(
    reconciliation_failure_history,
):
    sequence = reconciliation_failure_history
    first = sequence.first
    failed = sequence.fail()
    # The failed worker can observe an older run's cell. Its own cell was never reserved.
    sequence.observations(failed)
    current = first.add('Продолжи')
    states = (dict(sequence.cancelled.agent_state), dict(failed.agent_state))
    digest = first.original.agent_state['source_checkpoint']['digest']

    assert await first.resume(current) == first.expected
    assert current.agent_state['source_checkpoint_resume'] == {
        'previous_run_id': str(first.original.id), 'source_run_id': str(first.original.id),
        'digest': digest, 'baseline_sha': None,
    }
    first.finish(current, 'failed')
    retry = first.add('Продолжи')
    assert await first.resume(retry) == first.expected
    assert retry.agent_state['source_checkpoint_resume']['previous_run_id'] == str(current.id)
    assert first.reads.call_count == 2
    assert (sequence.cancelled.agent_state, failed.agent_state) == states
    assert first.project.current_snapshot_id is None


@pytest.mark.parametrize('change', [
    'error', 'amended', 'owner', 'snapshot', 'admitted', 'admitted_token', 'claim',
    'no_worker_execution', 'operation', 'operation_for_another_run', 'response',
    'project_snapshot', 'malformed_observation', 'own_observation', 'active_observation',
    'quiescent_observation', 'lost_alias_observation', 'late_observation',
])
async def test_first_build_source_does_not_cross_unproven_reconciliation_failure(
    history, reconciliation_failure_history, change,  # noqa: F811
):
    from uuid import uuid4

    sequence = reconciliation_failure_history
    first = sequence.first
    prompt = 'Продолжи, но поменяй формулу' if change == 'amended' else 'Продолжи сборку'
    failed = sequence.fail(prompt, user=uuid4() if change == 'owner' else None)
    first_observation = sequence.observations(failed)[0]
    current = first.add('Продолжи')
    if change == 'error':
        failed.error += '.'
    elif change == 'snapshot':
        failed.agent_state = {**failed.agent_state, 'dispatch': {
            **failed.agent_state['dispatch'], 'current_snapshot_id': str(history.snapshot.id),
        }}
    elif change == 'admitted':
        failed.started_at = failed.created_at
    elif change == 'admitted_token':
        failed.agent_state = {**failed.agent_state, 'capacity_admitted_dispatch_token': None}
    elif change == 'claim':
        failed.agent_state = {**failed.agent_state, 'capacity_dispatch_claim': None}
    elif change == 'no_worker_execution':
        failed.execution_started_at = None
    elif change == 'operation':
        sequence.operation(generation_run_id=failed.id)
    elif change == 'operation_for_another_run':
        sequence.operation(generation_run_id=sequence.cancelled.id, execution_run=failed)
    elif change == 'response':
        failed.response_payload = {
            **failed.response_payload, 'snapshot_id': str(history.snapshot.id),
        }
    elif change == 'project_snapshot':
        first.project.current_snapshot_id = history.snapshot.id
    elif change == 'lost_alias_observation':
        first_observation.execution_run_id = current.id
    elif change == 'late_observation':
        first_observation.finished_at = current.created_at
    elif change.endswith('_observation'):
        operation = first_observation
        observed = sequence.cancelled
        if change == 'malformed_observation':
            operation.request_payload = {}
        elif change == 'own_observation':
            operation.generation_run_id = failed.id
        elif change == 'active_observation':
            payload = operation.result_payload
            operation.result_payload = {**payload, 'lease_observation': {
                **payload['lease_observation'], 'active_generation_run_id': str(observed.id),
                'active_generation_fencing_epoch': operation.fencing_epoch,
            }}
        elif change == 'quiescent_observation':
            payload = operation.result_payload
            operation.result_payload = {**payload, 'lease_observation': {
                **payload['lease_observation'], 'quiescent': True,
            }}
    history.session.commit()

    assert await first.resume(current) is None
    first.reads.assert_not_called()
    assert 'source_checkpoint_resume' not in current.agent_state


@pytest.mark.parametrize('intervening', ['unrelated', 'amended', 'completed', 'owner', 'baseline'])
async def test_resume_provenance_never_crosses_a_newer_unrelated_turn(
    history, checkpoints, intervening,  # noqa: F811 - pytest consumes imported fixture
):
    from uuid import uuid4

    save, resume = checkpoints
    original = history.add('Original product with orders', status='running')
    await save(original)
    resumed = history.add('продолжи', status='running')
    assert await resume(resumed) is not None
    resumed.status = 'failed'
    history.session.commit()
    if intervening == 'unrelated':
        history.add('Build a different application')
    elif intervening == 'amended':
        history.add('Продолжи, но измени цены')
    elif intervening == 'completed':
        resumed.status = 'completed'
    elif intervening == 'owner':
        resumed.user_id = uuid4()
    history.session.commit()
    current = history.add('продолжи', status='running')
    assert await resume(current, 'b' * 40 if intervening == 'baseline' else BASE) is None


@pytest.mark.parametrize('corruption', [
    'malformed', 'cycle', 'forward', 'cross_owner_source', 'cross_project_source',
    'unrelated_parent', 'digest', 'source_baseline', 'invalid_source', 'amended_message',
])
async def test_resume_provenance_rejects_invalid_or_foreign_source_links(
    history, checkpoints, corruption,  # noqa: F811 - pytest consumes imported fixture
):
    from uuid import uuid4

    from yleum_api.models.message import Message

    save, resume = checkpoints
    original = history.add('Original product with orders', status='running')
    await save(original)
    resumed = history.add('продолжи', status='running')
    assert await resume(resumed) is not None
    _mark_failed(history, resumed)
    marker = dict(resumed.agent_state['source_checkpoint_resume'])
    if corruption == 'malformed':
        marker['source_run_id'] = '../private-object'
    elif corruption == 'cycle':
        marker['source_run_id'] = str(resumed.id)
    elif corruption == 'forward':
        marker['source_run_id'] = str(uuid4())
    elif corruption == 'cross_owner_source':
        original.user_id = uuid4()
    elif corruption == 'cross_project_source':
        original.project_id = uuid4()
    elif corruption == 'unrelated_parent':
        marker['previous_run_id'] = str(uuid4())
    elif corruption == 'digest':
        marker['digest'] = 'f' * 64
    elif corruption == 'source_baseline':
        state = dict(original.agent_state['source_checkpoint'])
        state['baseline_sha'] = 'b' * 40
        original.agent_state = {'source_checkpoint': state}
    elif corruption == 'invalid_source':
        original.status = 'completed'
    elif corruption == 'amended_message':
        history.session.get(Message, resumed.user_message_id).content = 'Продолжи, измени цены'
    resumed.agent_state = {'source_checkpoint_resume': marker}
    history.session.commit()
    current = history.add('продолжи', status='running')
    assert await resume(current) is None


async def test_new_checkpoint_on_resumed_run_takes_precedence_over_inherited_source(
    history, checkpoints,  # noqa: F811 - pytest consumes imported fixture
):
    from uuid import uuid4

    from yleum_api.services.generation import source_checkpoint as sc
    from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline

    save, resume = checkpoints
    original = history.add('Original product with orders', status='running')
    await save(original)
    resumed = history.add('продолжи', status='running')
    assert await resume(resumed) is not None

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, model, key, **kwargs):
            return history.session.get(model, key, **kwargs)

        async def commit(self):
            history.session.commit()

    await sc.persist_checkpoint(
        factory=Session,
        ids=GenerationIds(resumed.id, resumed.project_id, resumed.user_id,
                          resumed.user_message_id, uuid4()),
        baseline=SourceBaseline(None, BASE, {}),
        files={'src/app/page.tsx': 'saved feature plus repaired orders'},
        original_prompt='Original product with orders',
    )
    _mark_failed(history, resumed)
    current = history.add('продолжи', status='running')
    assert await resume(current) == (
        {'files': {'src/app/page.tsx': 'saved feature plus repaired orders'}, 'deleted_paths': []},
        'Original product with orders',
    )


async def test_original_object_read_outage_does_not_strand_saved_source_on_next_resume(
    history, checkpoints, monkeypatch,  # noqa: F811 - pytest consumes imported fixture
):
    from yleum_api.services.generation import source_checkpoint as sc

    save, resume = checkpoints
    original = history.add('Original product with orders', status='running')
    await save(original)
    current = history.add('продолжи', status='running')

    def unavailable(*_):
        raise RuntimeError('private object read unavailable')

    with monkeypatch.context() as unavailable_storage:
        unavailable_storage.setattr(sc, '_get_checkpoint', unavailable)
        with pytest.raises(RuntimeError, match='private object read unavailable'):
            await resume(current)
    _mark_failed(history, current)
    retry = history.add('продолжи', status='running')
    assert await resume(retry) == (
        {'files': {'src/app/page.tsx': 'saved feature', 'src/app/empty.ts': ''},
         'deleted_paths': ['src/app/old.ts']},
        'Original product with orders',
    )


async def test_invalid_original_object_never_returns_resumable_source(
    history, checkpoints, monkeypatch,  # noqa: F811 - pytest consumes imported fixture
):
    from yleum_api.services.generation import source_checkpoint as sc

    save, resume = checkpoints
    original = history.add('Original product with orders', status='running')
    await save(original)
    monkeypatch.setattr(sc, '_get_checkpoint', lambda *_: b'{"files": "invalid"}')
    current = history.add('продолжи', status='running')
    assert await resume(current) is None
    _mark_failed(history, current)
    retry = history.add('продолжи', status='running')
    assert await resume(retry) is None


async def test_resume_reference_commit_failure_stops_before_private_source_read(
    history, checkpoints, monkeypatch,  # noqa: F811 - pytest consumes imported fixture
):
    from yleum_api.services.generation import source_checkpoint as sc

    save, resume = checkpoints
    original = history.add('Original product with orders', status='running')
    await save(original)
    current = history.add('продолжи', status='running')

    def unavailable():
        raise RuntimeError('provenance commit unavailable')

    def forbidden_read(*_):
        pytest.fail('Source must not be returned or read before provenance is durable')

    monkeypatch.setattr(history.session, 'commit', unavailable)
    monkeypatch.setattr(sc, '_get_checkpoint', forbidden_read)
    with pytest.raises(RuntimeError, match='provenance commit unavailable'):
        await resume(current)
    history.session.rollback()


def _encode(files=None):
    from yleum_api.services.generation.source_checkpoint import encode_checkpoint

    return encode_checkpoint(
        project_id=PROJECT, user_id=OWNER, run_id=RUN, baseline_sha=BASE,
        files=files or {'src/app/page.tsx': 'draft', 'src/app/empty.ts': ''},
        deleted_paths=['src/app/old.ts'],
    )


def _decode(data, **kwargs):
    from yleum_api.services.generation.source_checkpoint import decode_checkpoint

    return decode_checkpoint(data, project_id=PROJECT, user_id=OWNER, run_id=RUN,
                             baseline_sha=kwargs.get('baseline_sha', BASE))


def test_checkpoint_preserves_exact_source_empty_files_and_deletions():
    result = _decode(_encode())
    assert result is not None
    assert result['files'] == {'src/app/page.tsx': 'draft', 'src/app/empty.ts': ''}
    assert result['deleted_paths'] == ['src/app/old.ts']


@pytest.mark.parametrize('field,value', [
    ('project_id', str(RUN)), ('user_id', str(RUN)), ('run_id', str(OWNER)),
    ('baseline_sha', 'b' * 40), ('version', 999),
])
def test_checkpoint_rejects_wrong_identity_or_baseline(field, value):
    payload = json.loads(_encode())
    payload[field] = value
    assert _decode(json.dumps(payload).encode()) is None


@pytest.mark.parametrize('path', ['../foreign.ts', '/root/key', '.git/config', '.env'])
def test_checkpoint_rejects_unsafe_source_paths(path):
    with pytest.raises(ValueError):
        _encode({path: 'private'})


def test_checkpoint_rejects_non_string_file_content():
    payload = json.loads(_encode())
    payload['files']['src/app/page.tsx'] = {'content': 'draft'}
    assert _decode(json.dumps(payload).encode()) is None


@pytest.mark.parametrize('intervening', ['none', 'completed', 'owner', 'baseline'])
async def test_only_adjacent_owned_failed_checkpoint_can_resume(
    history, monkeypatch, intervening,  # noqa: F811 - pytest consumes imported fixture
):
    from uuid import uuid4

    from yleum_api.services.generation import source_checkpoint as sc
    from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline

    previous = history.add('Original application request')
    previous.agent_state = {'source_checkpoint': {'digest': 'd' * 64, 'baseline_sha': BASE}}
    history.session.commit()
    if intervening == 'completed':
        history.add('A newer finished change', status='completed')
    if intervening == 'owner':
        history.add('Another owner', user=uuid4())
    current = history.add('продолжи', status='running')

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, model, key, **kwargs):
            return history.session.get(model, key, **kwargs)

        async def scalar(self, statement):
            return history.session.scalar(statement)

        async def commit(self):
            history.session.commit()

    data = sc.encode_checkpoint(
        project_id=previous.project_id, user_id=previous.user_id, run_id=previous.id,
        baseline_sha=BASE, files={'src/app/page.tsx': 'saved draft'}, deleted_paths=[],
    )
    monkeypatch.setattr(sc, '_get_checkpoint', lambda *_: data)
    result = await sc.adjacent_checkpoint(
        factory=Session,
        ids=GenerationIds(current.id, current.project_id, current.user_id,
                          current.user_message_id, uuid4()),
        baseline=SourceBaseline(None, 'b' * 40 if intervening == 'baseline' else BASE, {}),
    )
    if intervening == 'none':
        assert result == ({'files': {'src/app/page.tsx': 'saved draft'}, 'deleted_paths': []},
                          'Original application request')
    else:
        assert result is None


async def test_private_checkpoint_roundtrip_preserves_the_accepted_snapshot(
    history, monkeypatch,  # noqa: F811 - pytest consumes imported fixture
):
    import io
    from uuid import uuid4

    from sqlalchemy import select

    from yleum_api.models.snapshot import Snapshot
    from yleum_api.services.generation import source_checkpoint as sc
    from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline

    objects = {}

    class Response(io.BytesIO):
        def release_conn(self):
            pass

    class ObjectStore:
        def put_object(self, bucket, key, data, length, **kwargs):
            objects[(bucket, key)] = data.read(length)

        def get_object(self, bucket, key):
            return Response(objects[(bucket, key)])

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, model, key, **kwargs):
            return history.session.get(model, key, **kwargs)

        async def scalar(self, statement):
            return history.session.scalar(statement)

        async def commit(self):
            history.session.commit()

    monkeypatch.setattr(sc, 'get_minio_client', lambda: ObjectStore())
    accepted_before = list(history.session.scalars(select(Snapshot.id)))
    previous = history.add('Original product', status='running')
    baseline = SourceBaseline(None, BASE, {'src/app/old.ts': 'old'})
    await sc.persist_checkpoint(
        factory=Session,
        ids=GenerationIds(previous.id, previous.project_id, previous.user_id,
                          previous.user_message_id, uuid4()),
        baseline=baseline, files={'src/app/page.tsx': 'draft', 'src/app/empty.ts': ''},
        original_prompt='Original product',
    )
    previous.status = 'failed'
    history.session.commit()
    current = history.add('продолжи', status='running')
    restored = await sc.adjacent_checkpoint(
        factory=Session,
        ids=GenerationIds(current.id, current.project_id, current.user_id,
                          current.user_message_id, uuid4()), baseline=baseline,
    )
    assert restored == ({'files': {'src/app/page.tsx': 'draft', 'src/app/empty.ts': ''},
                         'deleted_paths': ['src/app/old.ts']}, 'Original product')
    assert list(history.session.scalars(select(Snapshot.id))) == accepted_before
    assert len(objects) == 1


@pytest.mark.parametrize('prompt,want', [
    ('продолжи', True), ('попробуй ещё раз', True),
    ('Продолжи, но измени цены', False), ('Не продолжи, а создай другой экран', False),
])
def test_checkpoint_resume_does_not_replace_an_amended_request(prompt, want):
    from yleum_api.services.generation.source_checkpoint import checkpoint_resume_request

    assert checkpoint_resume_request(prompt) is want


@pytest.mark.parametrize('owner_prompt,resumes', [
    ('Попробуй еще раз', True),
    ('Продолжи, но измени цены', False),
])
async def test_pipeline_uses_original_retry_intent_before_expanded_discovery_prompt(
    history, checkpoints, monkeypatch, owner_prompt, resumes,  # noqa: F811
):
    """Actual pipeline + SQL history + checkpoint bytes after admitted runtime."""
    from types import SimpleNamespace
    from uuid import uuid4

    from yleum_api.services.generation import agent_pipeline, recovery_execution
    from yleum_api.services.generation import recovery_requirements as requirements
    from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline

    save, _ = checkpoints
    original = history.add('Original product with orders', status='running')
    await save(original)
    current = history.add(owner_prompt, status='running')
    expanded = 'Expanded onboarding transcript: build a complete new application.'
    expected = 'Original product with orders' if resumes else expanded

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, *args, **kwargs):
            return history.session.get(*args, **kwargs)

        async def scalar(self, statement):
            return history.session.scalar(statement)

        async def commit(self):
            history.session.commit()

    async def no_execution(*_args, **_kwargs):
        return None

    async def admitted_runtime(**_kwargs):
        assert current.status == 'running'
        return SimpleNamespace(vision_context=expanded), None

    class ReachedClassification(Exception):
        pass

    async def classified(**kwargs):
        assert kwargs['prompt_text'] == expected
        marker = (current.agent_state or {}).get('source_checkpoint_resume')
        assert (marker is not None) is resumes
        if resumes:
            assert marker['source_run_id'] == str(original.id)
        raise ReachedClassification

    monkeypatch.setattr(requirements, 'freeze_for_run', no_execution)
    monkeypatch.setattr(recovery_execution, 'initialize_execution', no_execution)
    monkeypatch.setattr(agent_pipeline, 'prepare_agent_runtime', admitted_runtime)
    monkeypatch.setattr(agent_pipeline, 'classify_agent_turn', classified)
    ids = GenerationIds(current.id, current.project_id, current.user_id,
                        current.user_message_id, uuid4())
    with pytest.raises(ReachedClassification):
        await agent_pipeline.run_agent_generation(
            _consume_free_generation=None, baseline=SourceBaseline(None, BASE, {}),
            capacity_dispatch_token=None, factory=Session, force_model=None, ids=ids,
            is_free=False, model_id='claude-sonnet-5.5', orchestrate=True, progress=None,
            project_info=SimpleNamespace(template='max_miniapp'), prompt_text=expanded,
            runtime=None, selected_elements=None,
        )


@pytest.fixture
async def legacy_checkpoints(history, monkeypatch):  # noqa: F811
    """Real SQLite admissions, snapshot and immutable checkpoint object storage."""
    import io
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy import JSON, Column, MetaData, Table, select
    from sqlalchemy.dialects.postgresql import JSONB

    from yleum_api.models.message import Message
    from yleum_api.models.project import Project
    from yleum_api.models.snapshot import Snapshot
    from yleum_api.services.generation import source_checkpoint as sc
    from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline
    from yleum_api.services.generation_runs import GenerationDispatch, store_generation_dispatch

    metadata = MetaData()
    Table(Project.__tablename__, metadata, *(Column(
        column.name, JSON() if isinstance(column.type, JSONB) else column.type,
        primary_key=column.primary_key,
    ) for column in Project.__table__.columns))
    metadata.create_all(history.session.get_bind())
    original = history.add('Original Cartel product with orders', status='running')
    original.id = UUID('ffffffff-ffff-4fff-afff-ffffffffffff')
    snapshot = history.session.scalar(select(Snapshot))
    snapshot.commit_sha = BASE
    project = Project(id=original.project_id, owner_id=original.user_id, name='Cartel',
                      slug='cartel', template='max_miniapp', current_snapshot_id=snapshot.id)
    history.session.add(project)
    baseline = SourceBaseline(snapshot.id, BASE, {'src/app/old.ts': 'accepted old source'})
    objects = {}
    reads = []

    class Response(io.BytesIO):
        def release_conn(self):
            pass

    class Storage:
        def put_object(self, bucket, key, data, length, **kwargs):
            objects[bucket, key] = data.read(length)

        def get_object(self, bucket, key):
            reads.append(key)
            return Response(objects[bucket, key])

    def admit(run):
        assistant = Message(id=uuid4(), project_id=run.project_id, role='assistant',
                            content='', created_at=run.created_at)
        history.session.add(assistant)
        run.assistant_message_id = assistant.id
        store_generation_dispatch(run, GenerationDispatch(
            schema_version=1, project_id=run.project_id, user_id=run.user_id,
            user_message_id=run.user_message_id, assistant_message_id=assistant.id,
            current_snapshot_id=snapshot.id,
            prompt_text='Expanded discovery transcript, not the original retry message.',
            model_id='test-no-provider', force_model=None, is_free=False,
            orchestrate=True, selected_elements=None,
        ))
        history.session.commit()
        return run

    def add(prompt='Попробуй еще раз', **kwargs):
        return admit(history.add(prompt, **kwargs))

    async def resume(run, value=baseline):
        return await sc.adjacent_checkpoint(
            factory=history.factory,
            ids=GenerationIds(run.id, run.project_id, run.user_id,
                              run.user_message_id, run.assistant_message_id), baseline=value,
        )

    monkeypatch.setattr(sc, 'get_minio_client', lambda: Storage())
    admit(original)
    await sc.persist_checkpoint(
        factory=history.factory,
        ids=GenerationIds(original.id, original.project_id, original.user_id,
                          original.user_message_id, original.assistant_message_id),
        baseline=baseline,
        files={'src/app/page.tsx': 'Cartel saved source', 'src/app/empty.ts': ''},
        original_prompt='Original Cartel product with orders',
    )
    original.status = 'failed'
    history.session.commit()
    return SimpleNamespace(original=original, add=add, resume=resume, objects=objects,
                           reads=reads, snapshot=snapshot, project=project, baseline=baseline)


@pytest.mark.parametrize(('interruption_valid', 'admitted_retries', 'allowed'), [
    (True, 1, True), (False, 1, False), (True, 18, True), (True, 19, False),
])
async def test_legacy_resume_rechecks_reconciliation_failure_beneath_unmarked_admitted_retry(
    history, legacy_checkpoints, reconciliation_operations, interruption_valid,  # noqa: F811
    admitted_retries, allowed,
):
    from datetime import timedelta

    legacy = legacy_checkpoints
    for_project, _, observations = reconciliation_operations
    workspace = for_project(legacy.project)
    legacy.original.finished_at = legacy.original.created_at + timedelta(milliseconds=500)
    failed = _mark_reconciliation_failure(history, legacy.add('Продолжи сборку'))
    alias = observations(workspace, legacy.original, failed)[0]
    if not interruption_valid:
        alias.execution_run_id = None
    for _ in range(admitted_retries):
        admitted = legacy.add('Продолжи сборку')
        admitted.started_at = admitted.created_at
    current = legacy.add('Продолжи', status='running')

    restored = await legacy.resume(current)
    if allowed:
        expected = ({'files': {'src/app/page.tsx': 'Cartel saved source', 'src/app/empty.ts': ''},
                     'deleted_paths': ['src/app/old.ts']}, 'Original Cartel product with orders')
        assert restored == expected
        marker = current.agent_state['source_checkpoint_resume']
        assert marker['previous_run_id'] == str(admitted.id)
        assert marker['source_run_id'] == str(legacy.original.id)
        _mark_failed(history, current)
        assert await legacy.resume(legacy.add('Продолжи', status='running')) == expected
    else:
        assert restored is None
        assert not legacy.reads and 'source_checkpoint_resume' not in current.agent_state


async def test_unmarked_legacy_retry_recovers_nearest_checkpoint_without_mutating_history(
    history, legacy_checkpoints,  # noqa: F811
):
    """Actual old6f checkpoint -> unmarked e201 standalone retry -> new admission."""
    from sqlalchemy import select

    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.snapshot import Snapshot

    legacy = legacy_checkpoints
    failed = legacy.add()  # expanded dispatch, but immutable message is standalone retry
    prior = {row.id: json.dumps(row.agent_state, sort_keys=True)
             for row in history.session.scalars(select(GenerationRun))}
    objects = dict(legacy.objects)
    snapshots = list(history.session.scalars(select(Snapshot.id)))
    current = legacy.add('Продолжи', status='running')
    expected = ({'files': {'src/app/page.tsx': 'Cartel saved source', 'src/app/empty.ts': ''},
                 'deleted_paths': ['src/app/old.ts']}, 'Original Cartel product with orders')
    assert await legacy.resume(current) == expected
    marker = current.agent_state['source_checkpoint_resume']
    assert marker['previous_run_id'] == str(failed.id)
    assert marker['source_run_id'] == str(legacy.original.id)
    assert len(legacy.reads) == 1
    after = {key: json.dumps(history.session.get(GenerationRun, key).agent_state, sort_keys=True)
             for key in prior}
    assert after == prior and legacy.objects == objects
    assert list(history.session.scalars(select(Snapshot.id))) == snapshots
    assert legacy.project.current_snapshot_id == legacy.baseline.snapshot_id
    # The new explicit link must remain usable even though its old parent stays unmarked.
    _mark_failed(history, current)
    retry = legacy.add('Попробуй еще раз', status='running')
    assert await legacy.resume(retry) == expected


@pytest.mark.parametrize('key,value', [
    ('source_checkpoint', None), ('source_checkpoint', {}), ('source_checkpoint', []),
    ('source_checkpoint_resume', None), ('source_checkpoint_resume', {}),
    ('source_checkpoint_resume', {'source_run_id': 'not-a-UUID'}),
])
async def test_legacy_fallback_never_bypasses_explicit_malformed_checkpoint_keys(
    history, legacy_checkpoints, key, value,  # noqa: F811
):
    legacy = legacy_checkpoints
    failed = legacy.add()
    failed.agent_state = {**failed.agent_state, key: value}
    history.session.commit()
    current = legacy.add(status='running')
    assert await legacy.resume(current) is None
    assert not legacy.reads and 'source_checkpoint_resume' not in current.agent_state


@pytest.mark.parametrize('mutation', [
    'amended', 'completed', 'owner', 'mode', 'foreign_message', 'missing_dispatch',
    'dispatch_message', 'dispatch_snapshot', 'missing_snapshot', 'snapshot_project',
    'snapshot_sha', 'project_snapshot', 'project_owner', 'equal_timestamp',
    'missing_current_dispatch', 'current_baseline', 'source_dispatch',
])
async def test_legacy_chain_requires_every_original_admission_and_unchanged_baseline(
    history, legacy_checkpoints, mutation,  # noqa: F811
):
    from uuid import uuid4

    from yleum_api.models.message import Message
    from yleum_api.services.generation.contracts import SourceBaseline

    legacy = legacy_checkpoints
    failed = legacy.add()
    current = legacy.add(status='running')
    baseline = legacy.baseline
    if mutation == 'amended':
        history.session.get(Message, failed.user_message_id).content = 'Продолжи, но измени цены'
    elif mutation == 'completed':
        failed.status = 'completed'
    elif mutation == 'owner':
        failed.user_id = uuid4()
    elif mutation == 'mode':
        failed.response_mode = 'chat'
    elif mutation == 'foreign_message':
        history.session.get(Message, failed.user_message_id).project_id = uuid4()
    elif mutation in {'missing_dispatch', 'missing_current_dispatch', 'source_dispatch'}:
        target = {'missing_dispatch': failed, 'missing_current_dispatch': current,
                  'source_dispatch': legacy.original}[mutation]
        target.agent_state = {key: value for key, value in target.agent_state.items()
                              if key != 'dispatch'}
    elif mutation in {'dispatch_message', 'dispatch_snapshot'}:
        dispatch = dict(failed.agent_state['dispatch'])
        key = 'user_message_id' if mutation == 'dispatch_message' else 'current_snapshot_id'
        dispatch[key] = str(uuid4())
        failed.agent_state = {**failed.agent_state, 'dispatch': dispatch}
    elif mutation == 'missing_snapshot':
        history.session.delete(legacy.snapshot)
    elif mutation == 'snapshot_project':
        legacy.snapshot.project_id = uuid4()
    elif mutation == 'snapshot_sha':
        legacy.snapshot.commit_sha = 'b' * 40
    elif mutation == 'project_snapshot':
        legacy.project.current_snapshot_id = uuid4()
    elif mutation == 'project_owner':
        legacy.project.owner_id = uuid4()
    elif mutation == 'equal_timestamp':
        failed.created_at = legacy.original.created_at
        failed.id = UUID('00000000-0000-4000-a000-000000000001')  # Old tie-break picks source.
    elif mutation == 'current_baseline':
        baseline = SourceBaseline(legacy.baseline.snapshot_id, 'b' * 40, {})
    history.session.commit()
    assert await legacy.resume(current, baseline) is None
    assert not legacy.reads and 'source_checkpoint_resume' not in current.agent_state


@pytest.mark.parametrize('intermediates,allowed', [(19, True), (20, False)])
async def test_legacy_resume_has_exact_twenty_predecessor_hop_budget(
    history, legacy_checkpoints, intermediates, allowed,  # noqa: F811
):
    legacy = legacy_checkpoints
    for _ in range(intermediates):
        legacy.add()
    current = legacy.add(status='running')
    assert (await legacy.resume(current) is not None) is allowed
    assert len(legacy.reads) == int(allowed)


async def test_legacy_resume_does_not_skip_nearer_broken_checkpoint(
    history, legacy_checkpoints, monkeypatch,  # noqa: F811
):
    legacy = legacy_checkpoints
    broken = legacy.add()
    broken.agent_state = {**broken.agent_state, 'source_checkpoint': {
        'digest': 'f' * 64, 'baseline_sha': BASE,
    }}
    history.session.commit()
    legacy.add()  # The compatibility walk must stop at that broken nearer object.
    current = legacy.add(status='running')
    # Nearest authoritative object is missing; the older valid object must not be read.
    from yleum_api.services.generation import source_checkpoint as sc
    monkeypatch.setattr(sc, '_get_checkpoint', lambda *_args: None)
    assert await legacy.resume(current) is None
    assert current.agent_state['source_checkpoint_resume']['source_run_id'] == str(broken.id)
    assert not legacy.reads


async def test_inherited_legacy_resume_still_requires_current_real_admitted_baseline(
    history, legacy_checkpoints,  # noqa: F811
):
    legacy = legacy_checkpoints
    legacy.add()
    resumed = legacy.add(status='running')
    assert await legacy.resume(resumed) is not None
    _mark_failed(history, resumed)
    current = legacy.add(status='running')
    current.agent_state = {}
    history.session.commit()
    reads = list(legacy.reads)
    assert await legacy.resume(current) is None
    assert legacy.reads == reads and not current.agent_state
