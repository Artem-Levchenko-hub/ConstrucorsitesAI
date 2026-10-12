"""Private unfinished source is not an accepted snapshot or a public export."""
import json
from uuid import UUID

import pytest

from tests.test_agent_retry_context import history  # noqa: F401 - real SQLite history fixture

PROJECT = UUID('11111111-1111-4111-8111-111111111111')
OWNER = UUID('22222222-2222-4222-8222-222222222222')
RUN = UUID('33333333-3333-4333-8333-333333333333')
BASE = 'a' * 40


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

        async def get(self, model, key):
            return history.session.get(model, key)

        async def scalar(self, statement):
            return history.session.scalar(statement)

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
