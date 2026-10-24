"""Exact provider facts survive reload without exposing a raw error or authorizing replay."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.test_generation_recovery_public import records
from yleum_api.core.errors import ApiError
from yleum_api.schemas.message import GenerationFailureDiagnostics
from yleum_api.services.generation.recovery_public import project_loaded
from yleum_api.services.generation_failure import public_generation_failure


def provider_step(**overrides):
    return {
        "kind": "provider_error", "ok": False, "http_status": 502,
        "upstream_http_status": 502, "error_code": "model_unavailable",
        "provider_charge_ambiguous": True, "will_retry": False,
        "detail": "secret-provider-body", **overrides,
    }


async def test_owner_status_exposes_exact_failure_before_canary_cleanup():
    data = records()
    data["run"].status = "failed"
    data["run"].error = "provider_http_502: private-provider-body"
    data["message"].agent_steps = [provider_step()]
    view = await project_loaded(**data)
    assert view.failure.code == "provider_unavailable"
    assert view.failure.retryable is False
    assert view.failure.diagnostics.model_dump(exclude_none=True) == {
        "error_code": "model_unavailable", "error_origin": "provider", "http_status": 502,
        "upstream_http_status": 502, "provider_charge_ambiguous": True, "retry_safe": False,
    }
    assert not view.acceptance.current_request_complete
    assert "private-provider-body" not in view.model_dump_json()
    assert "secret-provider-body" not in view.model_dump_json()


async def test_failure_projection_preserves_owner_and_message_scope():
    from uuid import uuid4

    data = records()
    data["run"].status = "failed"
    data["run"].error = "provider_http_502: private"
    data["message"].project_id = uuid4()
    with pytest.raises(ApiError):
        await project_loaded(**data)


@pytest.mark.parametrize("terminal", ["MAX_FINALIZATION_FAILED: bad build", "deadline"])
def test_previous_transient_provider_error_is_not_relabelled_as_final_cause(terminal):
    failure = public_generation_failure(
        SimpleNamespace(status="failed", error=terminal, agent_state={}),
        agent_steps=[provider_step()],
    )
    assert failure.diagnostics is None


def test_historical_numeric_marker_has_exact_status_but_no_invented_receipt():
    failure = public_generation_failure(SimpleNamespace(
        status="failed", error="provider_http_502: arbitrary-body", agent_state={},
    ))
    assert failure.diagnostics.model_dump(exclude_none=True) == {
        "error_origin": "provider", "upstream_http_status": 502,
    }


def test_gateway_status_is_distinct_from_upstream_and_cannot_grant_retry():
    failure = public_generation_failure(
        SimpleNamespace(status="failed", error="provider_http_502: arbitrary", agent_state={}),
        agent_steps=[provider_step(http_status=503, retry_safe=True)],
    )
    assert failure.diagnostics.http_status == 503
    assert failure.diagnostics.upstream_http_status == 502
    assert failure.diagnostics.retry_safe is False


def test_stale_or_contradictory_step_does_not_override_terminal_status():
    failure = public_generation_failure(
        SimpleNamespace(status="failed", error="provider_http_502: arbitrary", agent_state={}),
        agent_steps=[provider_step(upstream_http_status=401)],
    )
    assert failure.diagnostics.model_dump(exclude_none=True) == {
        "error_origin": "provider", "upstream_http_status": 502,
    }


@pytest.mark.parametrize("steps", [
    [provider_step(will_retry=True)],
    [provider_step(), {"kind": "step", "tool": "build", "ok": True}],
])
def test_generic_failure_does_not_borrow_an_earlier_provider_attempt(steps):
    failure = public_generation_failure(
        SimpleNamespace(status="failed", error="provider_request_failed: private", agent_state={}),
        agent_steps=steps,
    )
    assert failure.diagnostics.model_dump(exclude_none=True) == {"error_origin": "unknown"}


def test_timeout_never_inherits_previous_http_error_even_if_steps_were_truncated():
    failure = public_generation_failure(
        SimpleNamespace(status="failed", error="provider_timeout: private", agent_state={}),
        agent_steps=[provider_step()],
    )
    assert failure.diagnostics.upstream_http_status is None


@pytest.mark.parametrize("value", [None, "secret", {}, [None, [], "secret"]])
def test_malformed_historical_steps_do_not_break_status(value):
    failure = public_generation_failure(
        SimpleNamespace(status="failed", error="provider_request_failed: secret", agent_state={}),
        agent_steps=value,
    )
    assert "secret" not in failure.model_dump_json()


def test_arbitrary_codes_status_coercion_and_raw_messages_are_discarded():
    failure = public_generation_failure(
        SimpleNamespace(status="failed", error="provider_request_failed: secret", agent_state={}),
        agent_steps=[provider_step(
            error_code="secret", http_status=True, upstream_http_status="502",
            provider_charge_ambiguous="false", retry_safe="true",
        )],
    )
    assert failure.diagnostics.model_dump(exclude_none=True) == {"error_origin": "unknown"}


@pytest.mark.parametrize("payload", [
    {"http_status": True}, {"http_status": "502"}, {"upstream_http_status": 200},
    {"retry_safe": "true"}, {"error_code": "private"}, {"error_origin": "private"},
    {"body": "private"},
])
def test_public_diagnostics_schema_is_closed_and_strict(payload):
    with pytest.raises(ValidationError):
        GenerationFailureDiagnostics.model_validate(payload)


@pytest.mark.parametrize("capped_transcript", [False, True])
@pytest.mark.parametrize("terminal_rollback", [False, True])
async def test_rest_failed_generation_preserves_committed_provider_cause(
    client, db_session, capped_transcript, terminal_rollback,
):
    from tests.test_generation_runs import _owner_and_project
    from yleum_api.core.deps import get_current_user
    from yleum_api.main import app
    from yleum_api.models.generation_event import GenerationEvent
    from yleum_api.models.message import Message
    from yleum_api.services.generation_runs import reserve_generation_run

    owner, project = await _owner_and_project(db_session)
    run, _ = await reserve_generation_run(
        db_session, project_id=project.id, user_id=owner.id,
        idempotency_key="safe-provider-diagnostics", prompt="synthetic test",
    )
    message = Message(
        project_id=project.id, role="assistant", content="private-assistant-body",
        agent_steps=([{"kind": "step", "tool": "build", "ok": True}] * 200
                     if capped_transcript else [provider_step()]),
    )
    db_session.add(message)
    await db_session.flush()
    run.assistant_message_id = message.id
    run.status = "failed"
    run.error = "provider_http_502: private-provider-body"
    failed, rollback = correlated_steps({"run": run, "message": message})
    if capped_transcript or terminal_rollback:
        db_session.add(GenerationEvent(
            generation_run_id=run.id, project_id=project.id, message_id=message.id,
            seq=201, event_type="agent.step", payload=failed,
        ))
    if terminal_rollback:
        db_session.add(GenerationEvent(
            generation_run_id=run.id, project_id=project.id, message_id=message.id,
            seq=202, event_type="agent.step", payload=rollback,
        ))
    await db_session.commit()

    async def current_user():
        return owner

    app.dependency_overrides[get_current_user] = current_user
    try:
        response = await client.get(f"/api/projects/{project.id}/generation")
        assert response.status_code == 200
        body = response.json()
        assert body["failure"]["diagnostics"]["upstream_http_status"] == 502
        assert body["failure"]["diagnostics"]["http_status"] == 502
        assert body["failure"]["diagnostics"]["error_code"] == "model_unavailable"
        assert body["failure"]["diagnostics"]["retry_safe"] is False
        assert body["failure"]["code"] == "provider_unavailable"
        assert body["acceptance"]["current_request_complete"] is False
        assert "private-provider-body" not in response.text
        assert "private-assistant-body" not in response.text
        other, _ = await _owner_and_project(db_session)
        project.owner_id = other.id
        await db_session.commit()
        assert (await client.get(f"/api/projects/{project.id}/generation")).status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def terminal_public_sqlite():
    """Execute the actual owner projection against a real isolated SQLite journal."""
    from datetime import timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy import JSON, Column, MetaData, Table, create_engine
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.orm import Session

    from yleum_api.models.generation_billing import GenerationBillingIntent, GenerationBillingPolicy
    from yleum_api.models.generation_credits import GenerationCreditReservation
    from yleum_api.models.generation_event import GenerationEvent
    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.message import Message
    from yleum_api.models.project import Project
    from yleum_api.models.snapshot import Snapshot

    engine = create_engine('sqlite://')
    metadata = MetaData()
    for model in (Project, GenerationRun, Message, Snapshot, GenerationBillingPolicy,
                  GenerationRecoveryEffect, GenerationEvent, GenerationCreditReservation,
                  GenerationBillingIntent):
        Table(model.__tablename__, metadata, *(Column(
            column.name, JSON() if isinstance(column.type, JSONB) else column.type,
            primary_key=column.primary_key,
        ) for column in model.__table__.columns))
    metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    data = records()
    data['run'].status = 'failed'
    data['run'].error = 'provider_http_502: private-provider-body'
    session.add_all(data[name] for name in ('project', 'run', 'message', 'snapshot', 'policy'))
    session.commit()

    class AsyncSession:
        async def execute(self, statement):
            return session.execute(statement)

    def event(seq, payload, **overrides):
        row = GenerationEvent(
            id=uuid4(), generation_run_id=data['run'].id, project_id=data['project'].id,
            message_id=data['message'].id, event_type='agent.step', seq=seq, payload=payload,
            created_at=data['run'].finished_at - timedelta(seconds=30-seq % 30),
        )
        for key, value in overrides.items():
            setattr(row, key, value)
        session.add(row)
        session.commit()
        return row

    yield SimpleNamespace(data=data, engine=engine, session=session, async_session=AsyncSession(),
                          event=event)
    session.close()
    engine.dispose()


def correlated_steps(data):
    """Exact observed provider-request -> rollback contract; no provider raw body."""
    failed = provider_step(tool='provider_request', step=5, message_id=str(data['message'].id),
        recovery_trace={
            'run_id': str(data['run'].id), 'error_code': 'model_unavailable', 'will_retry': False,
            'http_status': 502, 'upstream_http_status': 502, 'error_origin': 'provider',
            'provider_charge_ambiguous': True,
        })
    rollback = {'kind': 'step', 'tool': 'rollback', 'ok': False, 'step': 5, 'path': '',
                'action': 'Сборка не завершена — сохраняю рабочую версию',
                'detail': 'private rollback detail', 'message_id': str(data['message'].id)}
    return failed, rollback


@pytest.mark.parametrize('capped', [False, True])
async def test_SQL_terminal_provider_then_rollback_retains_exact_cause_in_one_snapshot(
    terminal_public_sqlite, capped,
):
    from sqlalchemy import event

    from yleum_api.services.generation.recovery_public import project_generation_public

    fixture = terminal_public_sqlite
    data = fixture.data
    failed, rollback = correlated_steps(data)
    data['message'].agent_steps = ([{'kind': 'step', 'tool': 'build', 'ok': True}] * 200
                                   if capped else [failed, rollback])
    fixture.session.commit()
    fixture.event(201 if capped else 25, failed)
    fixture.event(202 if capped else 26, rollback)
    statements = []

    def executed(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(fixture.engine, 'before_cursor_execute', executed)
    try:
        view = await project_generation_public(fixture.async_session,
            project_id=data['project'].id, user_id=data['user_id'])
    finally:
        event.remove(fixture.engine, 'before_cursor_execute', executed)
    assert len(statements) == 1 and statements[0].lstrip().startswith('SELECT')
    assert view.failure.diagnostics.model_dump(exclude_none=True) == {
        'error_code': 'model_unavailable', 'error_origin': 'provider', 'http_status': 502,
        'upstream_http_status': 502, 'provider_charge_ambiguous': True, 'retry_safe': False,
    }
    assert not view.acceptance.current_request_complete
    assert 'private-provider-body' not in view.model_dump_json()
    assert 'secret-provider-body' not in view.model_dump_json()
    assert 'private rollback detail' not in view.model_dump_json()


def owned_terminal_events(data, payloads):
    from uuid import uuid4

    from yleum_api.models.generation_event import GenerationEvent

    return [GenerationEvent(
        id=uuid4(), generation_run_id=data['run'].id, project_id=data['project'].id,
        message_id=data['message'].id, event_type='agent.step', seq=25+index, payload=payload,
    ) for index, payload in enumerate(payloads)]


def failed_data():
    data = records()
    data['run'].status = 'failed'
    data['run'].error = 'provider_http_502: private'
    data['message'].agent_steps = [provider_step()]
    return data


def numeric_diagnostics(view):
    assert view.failure.diagnostics.model_dump(exclude_none=True) == {
        'error_origin': 'provider', 'upstream_http_status': 502,
    }
    assert not view.acceptance.current_request_complete


@pytest.mark.parametrize('suffix', [None, [], {}, [None], 'secret'])
async def test_explicit_authoritative_suffix_never_borrows_capped_legacy_steps(suffix):
    data = failed_data()
    numeric_diagnostics(await project_loaded(**data, terminal_events=suffix,
                                            latest_step=provider_step()))


async def test_existing_latest_step_keyword_and_absent_journal_keep_legacy_contract():
    data = failed_data()
    data['message'].agent_steps = []
    view = await project_loaded(**data, latest_step=provider_step())
    assert view.failure.diagnostics.http_status == 502
    data['message'].agent_steps = list(correlated_steps(data))
    # A legacy transcript cannot authorize skipping.
    numeric_diagnostics(await project_loaded(**data))


@pytest.mark.parametrize('edge', [
    'later_build', 'later_model', 'rollback_success', 'rollback_path', 'rollback_extra',
    'rollback_bool_step', 'provider_bool_step', 'different_step', 'missing_step',
    'provider_not_request', 'provider_ok', 'provider_retry', 'trace_retry', 'retry_string',
    'trace_contradiction', 'status_contradiction', 'malformed_trace', 'two_rollbacks',
    'reversed_events', 'equal_seq', 'bool_seq', 'wrong_type', 'different_terminal',
])
async def test_only_one_exact_same_request_rollback_can_be_skipped(edge):
    data = failed_data()
    failed, rollback = correlated_steps(data)
    if edge == 'later_build':
        rollback.update(tool='build', ok=True)
    elif edge == 'later_model':
        rollback.update(tool='provider_response')
    elif edge == 'rollback_success':
        rollback['ok'] = True
    elif edge == 'rollback_path':
        rollback['path'] = 'src/app.tsx'
    elif edge == 'rollback_extra':
        rollback['http_status'] = 502
    elif edge == 'rollback_bool_step':
        rollback['step'] = True
    elif edge == 'provider_bool_step':
        failed['step'] = True
    elif edge == 'different_step':
        rollback['step'] = 6
    elif edge == 'missing_step':
        failed.pop('step')
    elif edge == 'provider_not_request':
        failed['tool'] = 'build'
    elif edge == 'provider_ok':
        failed['ok'] = True
    elif edge == 'provider_retry':
        failed['will_retry'] = True
    elif edge == 'trace_retry':
        failed['recovery_trace']['will_retry'] = True
    elif edge == 'retry_string':
        failed['will_retry'] = 'false'
    elif edge == 'trace_contradiction':
        failed['recovery_trace']['error_code'] = 'wallet_empty'
    elif edge == 'status_contradiction':
        failed['upstream_http_status'] = failed['recovery_trace']['upstream_http_status'] = 401
    elif edge == 'malformed_trace':
        rollback['recovery_trace'] = None
    events = owned_terminal_events(data, [failed, rollback])
    if edge == 'two_rollbacks':
        events = owned_terminal_events(data, [failed, rollback, rollback])
    elif edge == 'reversed_events':
        events.reverse()
    elif edge == 'equal_seq':
        events[1].seq = events[0].seq
    elif edge == 'bool_seq':
        events[1].seq = True
    elif edge == 'wrong_type':
        events[1].event_type = 'generation.trace'
    elif edge == 'different_terminal':
        data['run'].error = 'MAX_FINALIZATION_FAILED: private'
    view = await project_loaded(**data, terminal_events=events)
    if edge == 'different_terminal':
        assert view.failure.diagnostics is None
    else:
        numeric_diagnostics(view)


@pytest.mark.parametrize('identity', [
    'run_id', 'generation_run_id', 'project_id', 'message_id', 'assistant_message_id',
    'snapshot_id',
])
@pytest.mark.parametrize('location', ['provider', 'provider_trace', 'rollback', 'rollback_trace'])
async def test_optional_payload_identities_must_match_the_owner_snapshot(identity, location):
    from uuid import uuid4

    data = failed_data()
    failed, rollback = correlated_steps(data)
    source = failed if location.startswith('provider') else rollback
    if location.endswith('_trace'):
        source = source.setdefault('recovery_trace', {})
    source[identity] = str(uuid4())
    events = owned_terminal_events(data, [failed, rollback])
    view = await project_loaded(**data, terminal_events=events)
    numeric_diagnostics(view)


async def test_matching_snapshot_ids_are_accepted_but_foreign_snapshot_is_not():
    from uuid import uuid4

    data = failed_data()
    failed, rollback = correlated_steps(data)
    for payload in (failed, rollback):
        payload['snapshot_id'] = str(data['snapshot'].id)
    events = owned_terminal_events(data, [failed, rollback])
    view = await project_loaded(**data, terminal_events=events)
    assert view.failure.diagnostics.http_status == 502
    data['snapshot'].project_id = uuid4()
    numeric_diagnostics(await project_loaded(**data, terminal_events=events))


@pytest.mark.parametrize('binding', ['generation_run_id', 'project_id', 'message_id'])
@pytest.mark.parametrize('position', [0, 1])
async def test_durable_event_foreign_bindings_are_barriers(binding, position):
    from uuid import uuid4

    data = failed_data()
    events = owned_terminal_events(data, correlated_steps(data))
    setattr(events[position], binding, uuid4())
    numeric_diagnostics(await project_loaded(**data, terminal_events=events))


@pytest.mark.parametrize('later', [None, 'work', 'rollback'])
async def test_SQL_null_or_later_work_never_walks_back_or_falls_back(terminal_public_sqlite, later):
    from yleum_api.services.generation.recovery_public import project_generation_public

    fixture = terminal_public_sqlite
    failed, rollback = correlated_steps(fixture.data)
    fixture.data['message'].agent_steps = [failed]
    fixture.session.commit()
    fixture.event(201, failed)
    if later is None:
        fixture.event(202, None)
    elif later == 'work':
        fixture.event(202, {'kind': 'step', 'tool': 'build', 'ok': True, 'step': 5})
        fixture.event(203, rollback)
    else:
        fixture.event(202, rollback)
        fixture.event(203, rollback)
    view = await project_generation_public(fixture.async_session,
        project_id=fixture.data['project'].id, user_id=fixture.data['user_id'])
    numeric_diagnostics(view)


@pytest.mark.parametrize('binding', ['generation_run_id', 'project_id', 'message_id'])
async def test_SQL_suffix_excludes_foreign_provider_evidence(terminal_public_sqlite, binding):
    from uuid import uuid4

    from yleum_api.services.generation.recovery_public import project_generation_public

    fixture = terminal_public_sqlite
    failed, rollback = correlated_steps(fixture.data)
    fixture.event(201, failed, **{binding: uuid4()})
    fixture.event(202, rollback)
    view = await project_generation_public(fixture.async_session,
        project_id=fixture.data['project'].id, user_id=fixture.data['user_id'])
    numeric_diagnostics(view)


@pytest.mark.parametrize('malformed', [None, [], 'private'])
def test_malformed_latest_legacy_step_is_a_barrier(malformed):
    failure = public_generation_failure(
        SimpleNamespace(status='failed', error='provider_http_502: private', agent_state={}),
        agent_steps=[provider_step(), malformed],
    )
    assert failure.diagnostics.model_dump(exclude_none=True) == {
        'error_origin': 'provider', 'upstream_http_status': 502,
    }


@pytest.mark.parametrize('earlier', [
    None, {'recovery_trace': None}, {'snapshot_id': 'old-version'},
])
async def test_SQL_latest_provider_does_not_depend_on_irrelevant_earlier_payload(
    terminal_public_sqlite, earlier,
):
    from yleum_api.services.generation.recovery_public import project_generation_public

    fixture = terminal_public_sqlite
    failed, _ = correlated_steps(fixture.data)
    fixture.event(201, earlier)
    fixture.event(202, failed)
    view = await project_generation_public(fixture.async_session,
        project_id=fixture.data['project'].id, user_id=fixture.data['user_id'])
    assert view.failure.diagnostics.error_code == 'model_unavailable'
    assert view.failure.diagnostics.http_status == 502
    assert view.failure.diagnostics.provider_charge_ambiguous is True
    assert view.failure.diagnostics.retry_safe is False
