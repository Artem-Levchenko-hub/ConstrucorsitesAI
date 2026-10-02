"""OWN C only, prepare-only default. Root READ ACK gates SELECT + existing SDK GET.
No command submission, reconciliation, release, runtime ensure, model or SQL data writes.
Private receipt0600 contains exact scoped IDs; stdout contains allowlists/hashes only.
"""
import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

RELEASE = '49b071162a5017374b96a193c23b9d48e4cf0995'
WORKSPACE_SHA = '43a24989fdbb7fa1d6845eb6d732894de5a0c366630226b57f9372445fe5e24d'
PROJECT_SHA = '9f9147a8ba0b192858dee0aae6650b8b491f504949603883150ebb552d67cda1'
RUN_SHA = 'e44f8ac5d1a5cf31a6ef3164fecc3e4fe18e5027ccaa4aff7c989774c04af915'
PURPOSE = 'OWN_C_READONLY_TERMINAL_DIAGNOSTIC'
PHASES = {'prepare', 'edit', 'fast_check', 'final_build', 'runtime_probe', 'snapshot',
          'promote', 'complete', 'full_build', 'build', 'bootstrap'}
FAILURE_PHASE = 'input'
RAW_IDS = {}

def sha(value):
    return hashlib.sha256(str(value).encode()).hexdigest()

def stamp(value):
    return value.isoformat() if value is not None else None

def label(value, allowed):
    return {'value': value if value in allowed else None,
            'unknown_present': value is not None and value not in allowed,
            'unknown_sha256': sha(value) if value is not None and value not in allowed else None}

def error(value):
    return {'present': bool(value), 'sha256': sha(value) if value else None}

def exception_metadata(exc):
    result = {'error_type': type(exc).__name__, 'failure_phase': FAILURE_PHASE}
    current = exc
    for _ in range(5):
        if current is None:
            break
        code = getattr(current, 'sqlstate', None) or getattr(current, 'pgcode', None)
        if isinstance(code, str) and re.fullmatch(r'[A-Z0-9]{5}', code):
            result['sqlstate'] = code
        current = getattr(current, 'orig', None) or getattr(current, '__cause__', None)
    return result

def scope(data):
    f = data['fixture']
    if f.get('fixture_label') != 'C' or f.get('owner_role') != 'user' or data.get('revision') != RELEASE:
        raise ValueError('Authorized C revision/role required')
    hashes = {k: f[k] for k in ('owner_id_sha256', 'project_id_sha256', 'run_id_sha256', 'workspace_id_sha256')}
    if not all(isinstance(v, str) and re.fullmatch(r'[0-9a-f]{64}', v) for v in hashes.values()):
        raise ValueError('Exact canonical UUID hash scope required')
    if hashes['project_id_sha256'] != PROJECT_SHA or hashes['run_id_sha256'] != RUN_SHA or hashes['workspace_id_sha256'] != WORKSPACE_SHA:
        raise ValueError('Frozen C project/run differs')
    binding = {'purpose': PURPOSE, 'revision': RELEASE, 'fixture_label': 'C', 'owner_role': 'user', **hashes}
    return hashes, sha(json.dumps(binding, sort_keys=True, separators=(',', ':')))

def validate(data):
    hashes, digest = scope(data)
    ack = data['read_ack']
    issued = datetime.fromisoformat(ack['issued_at_utc'].replace('Z', '+00:00'))
    if issued.tzinfo is None or not (0 <= (datetime.now(UTC) - issued).total_seconds() <= 900):
        raise ValueError('Fresh root READ ACK required')
    if ack.get('purpose') != PURPOSE or ack.get('scope_sha256') != digest:
        raise ValueError('Root READ ACK exact scope differs')
    return hashes, digest

def coordinator(raw):
    state = raw.get('max_finalization', {}) if isinstance(raw, dict) else {}
    state = state if isinstance(state, dict) else {}
    checkpoint = state.get('checkpoint', {})
    checkpoint = checkpoint if isinstance(checkpoint, dict) else {}
    return {'present': bool(state), 'phase': label(state.get('current_phase'), PHASES),
            'checkpoint_phase': label(checkpoint.get('phase'), PHASES),
            'outcome': label(state.get('outcome'), {'complete', 'completed', 'failed', 'cancelled', 'needs_edit', 'activating'}),
            'terminal_reason': error(state.get('terminal_reason')),
            'phase_ms': {k: v for k, v in state.get('phase_ms', {}).items() if k in PHASES and type(v) is int and v >= 0}
                        if isinstance(state.get('phase_ms'), dict) else {}}

async def probe(data):
    global FAILURE_PHASE
    hashes, digest = validate(data)
    logging.disable(logging.CRITICAL)
    from sqlalchemy import Text, cast, func, select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from yleum_api.core.config import get_settings
    from yleum_api.core.db import get_engine, dispose_engine
    from yleum_api.models.user import User
    from yleum_api.models.project import Project
    from yleum_api.models.generation_run import GenerationRun as Run
    from yleum_api.models.generation_event import GenerationEvent as Event
    from yleum_api.models.snapshot import Snapshot
    from yleum_api.models.project_cell import (ProjectCellWorkspace as Workspace, ProjectCellActivityLease as Activity,
                                               ProjectCellOperation as Operation, ProjectCellProof as Proof,
                                               ProjectCellProofResult as ProofResult)
    from yleum_api.models.usage import Usage
    from yleum_api.services import orchestrator_client as sdk

    FAILURE_PHASE = 'revision'
    if get_settings().omnia_release_sha != RELEASE:
        raise ValueError('Actual API revision differs')
    result = {'captured_utc': datetime.now(UTC).isoformat(), 'revision': RELEASE, 'scope_sha256': digest,
              'readonly': True, 'accepted': False, 'model_calls': 0, 'new_commands': 0, 'lifecycle_actions': 0,
              'max_sql_statements': 18, 'max_existing_journal_GETs': 3, 'sql_statements': 0,
              'scope_id_hashes': hashes, 'controller_journals': []}
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    journal_ids = []
    def uuid_hash(column):
        return func.encode(func.sha256(func.convert_to(cast(column, Text), 'UTF8')), 'hex')
    try:
        async with factory() as session:
            async def read(statement, phase):
                global FAILURE_PHASE
                FAILURE_PHASE = phase
                result['sql_statements'] += 1
                if result['sql_statements'] > 18:
                    raise ValueError('Read statement budget exceeded')
                return await session.execute(statement)
            await read(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'), 'readonly_transaction')
            await read(text("SET LOCAL statement_timeout='5000ms'"), 'statement_timeout')
            user = (await read(select(User.id, User.role).where(uuid_hash(User.id) == hashes['owner_id_sha256'],
                                                               User.role == 'user').limit(2), 'owner')).one()
            project = (await read(select(Project.id, Project.owner_id, Project.current_snapshot_id)
                                 .where(uuid_hash(Project.id) == PROJECT_SHA, Project.owner_id == user.id).limit(2), 'project')).one()
            run = (await read(select(Run.id, Run.project_id, Run.user_id, Run.status, Run.response_mode, Run.execution_backend,
                                    Run.created_at, Run.started_at, Run.execution_started_at, Run.finished_at, Run.error, Run.agent_state)
                             .where(uuid_hash(Run.id) == RUN_SHA, Run.project_id == project.id, Run.user_id == user.id).limit(2), 'run')).one()
            workspace = (await read(select(Workspace.id, Workspace.owner_id, Workspace.project_id, Workspace.provider,
                                          Workspace.state, Workspace.generation_run_id, Workspace.fencing_epoch,
                                          Workspace.last_error, Workspace.deleted_at)
                                   .where(uuid_hash(Workspace.id) == WORKSPACE_SHA, Workspace.project_id == project.id, Workspace.owner_id == user.id,
                                          Workspace.deleted_at.is_(None)).limit(2), 'workspace')).one()
            if workspace.provider != 'docker_owner_canary' or any(sha(value) != hashes[key] for value, key in
                  ((user.id, 'owner_id_sha256'), (project.id, 'project_id_sha256'), (run.id, 'run_id_sha256'), (workspace.id, 'workspace_id_sha256'))):
                raise ValueError('Authoritative exact C identity differs')
            RAW_IDS.update(owner_id=str(user.id), project_id=str(project.id), run_id=str(run.id), workspace_id=str(workspace.id))
            latest = (await read(select(Run.id).where(Run.project_id == project.id).order_by(Run.created_at.desc(), Run.id.desc()).limit(1), 'latest_run')).scalar_one()
            prior = (await read(select(func.count()).select_from(Run).where(Run.project_id == project.id,
                                  Run.created_at < run.created_at), 'first_run')).scalar_one()
            result['exact_scope'] = {'ordinary_owner': True, 'latest_run_same': latest == run.id,
                                     'first_run_for_project': prior == 0, 'workspace_bound': True}
            result['run'] = {'status': label(run.status, {'pending', 'queued_for_capacity', 'running', 'cancel_requested', 'completed', 'failed', 'cancelled'}),
                             'response_mode': label(run.response_mode, {'edit', 'build', 'clarify'}),
                             'execution_backend': label(run.execution_backend, {'api', 'worker'}),
                             'created_at': stamp(run.created_at), 'started_at': stamp(run.started_at),
                             'execution_started_at': stamp(run.execution_started_at), 'finished_at': stamp(run.finished_at), 'error': error(run.error)}
            result['workspace'] = {'id_sha256': sha(workspace.id), 'state': label(workspace.state, {'provisioning', 'ready', 'stopped', 'failed', 'deleting', 'deleted'}),
                                   'fencing_epoch': workspace.fencing_epoch, 'generation_lease_present': workspace.generation_run_id is not None,
                                   'lease_is_exact_run': workspace.generation_run_id == run.id,
                                   'current_lease_run_sha256': sha(workspace.generation_run_id) if workspace.generation_run_id else None,
                                   'error': error(workspace.last_error)}
            result['coordinator'] = coordinator(run.agent_state)
            kind = Event.payload['kind'].astext
            native = (await read(select(kind, func.count()).where(Event.generation_run_id == run.id, Event.project_id == project.id,
                                   Event.event_type == 'agent.step', kind.in_(('done', 'stalled'))).group_by(kind), 'native_boundaries')).all()
            result['native_boundary_event_counts'] = {k: count for k, count in native}
            activities = (await read(select(Activity).where(Activity.workspace_id == workspace.id, Activity.generation_run_id == run.id)
                                     .order_by(Activity.started_at.desc()).limit(10), 'activities')).scalars().all()
            result['activities'] = [{'operation_sha256': sha(a.operation_id), 'kind': label(a.kind, {'command', 'tool', 'finalization', 'snapshot', 'promotion'}),
                                     'state': label(a.state, {'active', 'completed', 'failed', 'timed_out', 'cancelled'}), 'phase': label(a.phase, PHASES),
                                     'fencing_epoch': a.fencing_epoch, 'started_at': stamp(a.started_at), 'heartbeat_at': stamp(a.heartbeat_at),
                                     'deadline_at': stamp(a.deadline_at), 'finished_at': stamp(a.finished_at), 'log_bytes': a.log_bytes,
                                     'error': error(a.redacted_diagnostic)} for a in activities]
            journal_ids = list(dict.fromkeys(a.operation_id for a in activities if a.kind in {'command', 'tool', 'finalization'}))[:3]
            operations = (await read(select(Operation.id, Operation.kind, Operation.status, Operation.attempt_count, Operation.fencing_epoch,
                                          Operation.created_at, Operation.started_at, Operation.finished_at, Operation.error)
                                    .where(Operation.workspace_id == workspace.id, Operation.generation_run_id == run.id)
                                    .order_by(Operation.created_at.desc()).limit(10), 'operations')).all()
            result['operations'] = [{'id_sha256': sha(o.id), 'kind': label(o.kind, {'ensure', 'wake', 'pause', 'stop', 'destroy', 'status', 'restore', 'reconcile', 'release'}),
                                     'status': label(o.status, {'pending', 'waiting_capacity', 'running', 'completed', 'failed', 'cancelled', 'indeterminate'}),
                                     'attempt_count': o.attempt_count, 'fencing_epoch': o.fencing_epoch,
                                     'created_at': stamp(o.created_at), 'started_at': stamp(o.started_at), 'finished_at': stamp(o.finished_at), 'error': error(o.error)} for o in operations]
            activity_scope = (Activity.workspace_id == workspace.id, Activity.generation_run_id == run.id)
            operation_scope = (Operation.workspace_id == workspace.id, Operation.generation_run_id == run.id)
            counts = (await read(select(select(func.count()).select_from(Activity).where(*activity_scope).scalar_subquery(),
                                       select(func.count()).select_from(Activity).where(*activity_scope, Activity.state == 'active').scalar_subquery(),
                                       select(func.count()).select_from(Operation).where(*operation_scope).scalar_subquery(),
                                       select(func.count()).select_from(Operation).where(*operation_scope, Operation.status.in_(('pending', 'waiting_capacity', 'running', 'indeterminate'))).scalar_subquery()), 'exact_unresolved_counts')).one()
            result['exact_run_counts'] = dict(zip(('activity_total', 'active_activity', 'operation_total', 'active_or_indeterminate_operation'), counts))
            dims = (await read(select(ProofResult.dimension, ProofResult.outcome, func.count()).join(Proof, Proof.id == ProofResult.proof_id)
                               .where(Proof.workspace_id == workspace.id, Proof.generation_run_id == run.id, ProofResult.workspace_id == workspace.id)
                               .group_by(ProofResult.dimension, ProofResult.outcome), 'proof_dimensions')).all()
            result['proof_dimensions'] = [{'dimension': label(d, {'bootstrap', 'fast_check', 'full_build', 'runtime', 'release'}),
                                           'outcome': label(o, {'green', 'red'}), 'count': count} for d, o, count in dims]
            result['snapshots'] = {'count': (await read(select(func.count()).select_from(Snapshot).where(Snapshot.project_id == project.id), 'snapshot_count')).scalar_one(),
                                   'current_snapshot_present': project.current_snapshot_id is not None}
            usage = (await read(select(func.count(Usage.id), func.coalesce(func.sum(Usage.cost_rub), 0),
                                      func.coalesce(func.sum(Usage.provider_cost_usd), 0), func.count(Usage.provider_cost_usd))
                                .where(Usage.user_id == user.id, Usage.project_id == project.id, Usage.run_id == run.id), 'usage_aggregate')).one()
            if any(not Decimal(str(v)).is_finite() or Decimal(str(v)) < 0 for v in usage[1:3]):
                raise ValueError('Nonfinite accounting aggregate')
            result['usage'] = {'rows': usage[0], 'cost_rub': str(usage[1]), 'provider_cost_usd': str(usage[2]), 'provider_priced_rows': usage[3]}
            workspace_id = workspace.id
        RAW_IDS['agent_operation_ids'] = [str(i) for i in journal_ids]
        FAILURE_PHASE = 'existing_journal_GET'
        for operation_id in journal_ids:
            entry = {'operation_sha256': sha(operation_id)}
            try:
                j = await asyncio.wait_for(sdk.project_cell_agent_operation_status(workspace_id, operation_id), timeout=8)
                if j.operation_id != operation_id or (j.terminal_response and j.terminal_response.operation_id != operation_id):
                    raise ValueError('Exact existing journal identity differs')
                terminal = j.terminal_response
                entry.update(state=label(j.state, {'running', 'completed', 'failed', 'timed_out', 'cancelled'}),
                             phase=label(j.phase, PHASES), log_bytes=j.log_bytes, heartbeat_at=stamp(j.heartbeat_at),
                             terminal_present=terminal is not None, terminal={'ok': terminal.ok, 'exit_code': terminal.exit_code,
                             'timed_out': terminal.timed_out, 'before_identity_present': terminal.before_identity is not None,
                             'after_identity_present': terminal.after_identity is not None, 'environment_mutated': terminal.environment_mutated,
                             'error': error(terminal.detail)} if terminal else None)
            except Exception as exc:
                entry['read_error'] = exception_metadata(exc)
            result['controller_journals'].append(entry)
        result['status'] = 'ACTUAL_OWN_C_READONLY_DIAGNOSTIC'
        result['finished_utc'] = datetime.now(UTC).isoformat()
        result['acceptance_note'] = 'Observed metadata only; no completed/ready/quiescent/launch acceptance inferred. Unknown labels preserved by presence/hash.'
        return result
    finally:
        await dispose_engine()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--scope-digest', action='store_true')
    parser.add_argument('--raw-output')
    args = parser.parse_args()
    if not args.execute and not args.scope_digest:
        print(json.dumps({'status': 'PREPARED_ONLY_NO_DB_NETWORK', 'revision': RELEASE, 'purpose': PURPOSE}))
        return
    fd = None
    try:
        data = json.loads(sys.stdin.read(65537))
        if args.scope_digest and not args.execute:
            print(json.dumps({'status': 'SCOPE_DIGEST_ONLY_NO_DB_NETWORK', 'scope_sha256': scope(data)[1]}))
            return
        validate(data)
        if not args.raw_output or not re.fullmatch(r'/(?:dev/shm|tmp)/qa-owned-c-[A-Za-z0-9_-]{1,80}\.json', args.raw_output):
            raise ValueError('Protected new C receipt path required')
        fd = os.open(args.raw_output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        result = asyncio.run(asyncio.wait_for(probe(data), timeout=60))
    except Exception as exc:
        result = {'status': 'READONLY_C_DIAGNOSTIC_BLOCKED', **exception_metadata(exc), 'accepted': False}
    if fd is not None:
        try:
            with os.fdopen(fd, 'w') as output:
                json.dump({'exact_scoped_identifiers_private': RAW_IDS, 'safe_summary': result}, output)
        except Exception as exc:
            result = {'status': 'READONLY_C_RECEIPT_WRITE_BLOCKED', **exception_metadata(exc), 'accepted': False}
    print(json.dumps(result))
    sys.exit(0 if result.get('status') == 'ACTUAL_OWN_C_READONLY_DIAGNOSTIC' else 2)

if __name__ == '__main__':
    main()
