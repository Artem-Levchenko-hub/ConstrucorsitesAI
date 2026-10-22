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
    """Actual pipeline + SQL history + checkpoint bytes, stopped before runtime/LLM."""
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
            factory=Session,
            ids=GenerationIds(run.id, run.project_id, run.user_id,
                              run.user_message_id, run.assistant_message_id), baseline=value,
        )

    monkeypatch.setattr(sc, 'get_minio_client', lambda: Storage())
    admit(original)
    await sc.persist_checkpoint(
        factory=Session,
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
