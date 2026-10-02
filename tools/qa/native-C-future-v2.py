"""OWN C only, prepare-only default. Root READ ACK gates SELECT + existing SDK GET.
No command submission, reconciliation, release, runtime ensure, model or SQL data writes.
Private receipt0600 contains exact scoped IDs; stdout contains allowlists/hashes only.
"""
import argparse
import contextlib
import io
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

OLD_RELEASE = '49b071162a5017374b96a193c23b9d48e4cf0995'
RELEASE = None
OWNER_SHA = 'f2ef40a428c9e57e4c215d8d6c0a55871d952ca201e6c3edb1be9f50dadbd066'
SNAPSHOT_SHA = '0cb1161b13b16407a15f8071e6aa376006c8ca732f9d7900393d330362442273'
VERSION_SHA = 'b316a1020c00743f24b1be79ba2f4752ae7b286a57544027d07a2d25f91eee1d'
SOURCE_SHA = 'cb2eaca7e61cf1a4446cd5b246a84c33b57beaef392b4fb4c5cc2063fc92e8a9'
WORKSPACE_SHA = '43a24989fdbb7fa1d6845eb6d732894de5a0c366630226b57f9372445fe5e24d'
PROJECT_SHA = '9f9147a8ba0b192858dee0aae6650b8b491f504949603883150ebb552d67cda1'
RUN_SHA = 'e44f8ac5d1a5cf31a6ef3164fecc3e4fe18e5027ccaa4aff7c989774c04af915'
PURPOSE = 'OWN_C_FUTURE_RELEASE_NATIVE_READONLY'
PHASES = {'prepare', 'edit', 'fast_check', 'final_build', 'runtime_probe', 'snapshot',
          'promote', 'complete', 'full_build', 'build', 'bootstrap'}
FAILURE_PHASE = 'input'
RAW_IDS = {}
PRIVATE_PROBE_OUTPUT = {}

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
    if not isinstance(RELEASE, str) or not re.fullmatch('[0-9a-f]{40}', RELEASE) or RELEASE == OLD_RELEASE:
        raise ValueError('Future full revision required')
    cid = data.get('api_container_id')
    if not isinstance(cid, str) or not re.fullmatch('[0-9a-f]{64}', cid):
        raise ValueError('Root actual API container identity required')
    f = data['fixture']
    if f.get('fixture_label') != 'C' or f.get('owner_role') != 'user' or data.get('revision') != RELEASE:
        raise ValueError('Authorized C revision/role required')
    hashes = {k: f[k] for k in ('owner_id_sha256', 'project_id_sha256', 'run_id_sha256', 'workspace_id_sha256')}
    if not all(isinstance(v, str) and re.fullmatch(r'[0-9a-f]{64}', v) for v in hashes.values()):
        raise ValueError('Exact canonical UUID hash scope required')
    if hashes['owner_id_sha256'] != OWNER_SHA or hashes['project_id_sha256'] != PROJECT_SHA or hashes['run_id_sha256'] != RUN_SHA or hashes['workspace_id_sha256'] != WORKSPACE_SHA:
        raise ValueError('Frozen C project/run differs')
    binding = {'purpose': PURPOSE, 'revision': RELEASE, 'fixture_label': 'C', 'owner_role': 'user', 'api_container_id': cid, 'helper_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'selected_host': 'commerce', 'original_snapshot_sha256': SNAPSHOT_SHA, 'original_version_sha256': VERSION_SHA, 'max_sql_statements': 18, 'max_existing_journal_GETs': 10,
               'journal_coverage': 'complete_exact_first_run_native_activities', **hashes}
    return hashes, sha(json.dumps(binding, sort_keys=True, separators=(',', ':')))

def validate(data):
    hashes, digest = scope(data)
    ack = data['read_ack']
    issued = datetime.fromisoformat(ack['issued_at_utc'].replace('Z', '+00:00'))
    if issued.tzinfo is None or not (0 <= (datetime.now(UTC) - issued).total_seconds() <= 300):
        raise ValueError('Fresh root READ ACK required')
    if (ack.get('purpose') != PURPOSE or ack.get('scope_sha256') != digest
            or ack.get('helper_sha256') != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            or ack.get('root_readonly_authorized') is not True or ack.get('production_delivered') is not True
            or ack.get('exclusive_actor') is not True or ack.get('inprogress') is not False
            or ack.get('model_calls_authorized') is not False):
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

class BoundedCapture(io.StringIO):
    LIMIT = 1024**2
    def __init__(self):
        super().__init__()
        self.used = 0
    def write(self, value):
        encoded = value.encode('utf-8', errors='replace')
        remaining = self.LIMIT - self.used
        accepted = encoded[:remaining]
        super().write(accepted.decode('utf-8', errors='ignore'))
        self.used += len(accepted)
        if len(encoded) > remaining:
            raise ValueError('Protected output budget exceeded')
        return len(value)


async def protected_probe(data):
    # Whole fresh-process product import/probe scope. Capture every ordinary print
    # and silence structlog BEFORE SDK imports; stdlib disable alone is insufficient.
    stdout, stderr = BoundedCapture(), BoundedCapture()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            import structlog
            structlog.configure(logger_factory=structlog.ReturnLoggerFactory())
            return await probe(data)
    finally:
        PRIVATE_PROBE_OUTPUT.update(stdout=stdout.getvalue(), stderr=stderr.getvalue(),
                                    stdout_bytes=stdout.used, stderr_bytes=stderr.used)


def unfinished_predicate(state_column, finished_column, terminal_states):
    from sqlalchemy import or_
    # Canonical drain treats NULL finished_at as unfinished, even with terminal state.
    return or_(state_column.notin_(terminal_states), finished_column.is_(None))


async def probe(data):
    global FAILURE_PHASE
    hashes, digest = validate(data)
    logging.disable(logging.CRITICAL)
    from sqlalchemy import Text, cast, func, select, text, or_, and_
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from yleum_api.core.config import get_settings
    from yleum_api.core.db import get_engine, dispose_engine
    from yleum_api.models.user import User
    from yleum_api.models.project import Project
    from yleum_api.models.generation_run import GenerationRun as Run
    from yleum_api.models.generation_event import GenerationEvent as Event
    from yleum_api.models.snapshot import Snapshot
    from yleum_api.models.project_version import ProjectVersion
    from yleum_api.models.usage_settlement import UsageSettlement
    from yleum_api.models.project_cell import (ProjectCellWorkspace as Workspace, ProjectCellActivityLease as Activity,
                                               ProjectCellOperation as Operation, ProjectCellProof as Proof,
                                               ProjectCellProofResult as ProofResult)
    from yleum_api.models.usage import Usage
    from yleum_api.services import orchestrator_client as sdk
    from yleum_api.services import orchestrator_hosts as hosts

    FAILURE_PHASE = 'revision'
    if get_settings().omnia_release_sha != RELEASE:
        raise ValueError('Actual API revision differs')
    result = {'captured_utc': datetime.now(UTC).isoformat(), 'revision': RELEASE, 'scope_sha256': digest,
              'api_container_id_from_root_scope': data['api_container_id'], 'api_container_runtime_identity_verified': False,
              'readonly': True, 'accepted': False, 'model_calls': 0, 'new_commands': 0, 'lifecycle_actions': 0,
              'max_sql_statements': 18, 'max_existing_journal_GETs': 10, 'sql_statements': 0,
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
                                          Workspace.last_error, Workspace.deleted_at, Workspace.orchestrator)
                                   .where(uuid_hash(Workspace.id) == WORKSPACE_SHA, Workspace.project_id == project.id, Workspace.owner_id == user.id,
                                          Workspace.deleted_at.is_(None)).limit(2), 'workspace')).one()
            if workspace.provider != 'docker_owner_canary' or any(sha(value) != hashes[key] for value, key in
                  ((user.id, 'owner_id_sha256'), (project.id, 'project_id_sha256'), (run.id, 'run_id_sha256'), (workspace.id, 'workspace_id_sha256'))):
                raise ValueError('Authoritative exact C identity differs')
            reg = hosts.registry()
            selected = reg.get(workspace.orchestrator)
            if workspace.orchestrator != 'commerce' or reg.single is not False or selected.name != 'commerce':
                raise ValueError('Exact remembered Commerce routing differs')
            # Normal SDK routing cache ONLY, process-local; no DB write or additional lookup.
            hosts.remember_workspace_host(workspace.id, project.id, 'commerce')
            result['controller_placement'] = {'selected_host': 'commerce', 'registry_resolved_same_name': True,
                                              'registry_single': False, 'actual_controller_health_verified': False,
                                              'normal_sdk_existing_GETs_only': True}
            RAW_IDS.update(owner_id=str(user.id), project_id=str(project.id), run_id=str(run.id), workspace_id=str(workspace.id))
            latest = (await read(select(Run.id).where(Run.project_id == project.id).order_by(Run.created_at.desc(), Run.id.desc()).limit(1), 'latest_run')).scalar_one()
            prior = (await read(select(func.count()).select_from(Run).where(Run.project_id == project.id,
                                  or_(Run.created_at < run.created_at, and_(Run.created_at == run.created_at, Run.id < run.id))), 'first_run')).scalar_one()
            result['exact_scope'] = {'ordinary_owner': True, 'latest_run_same': latest == run.id,
                                     'first_run_for_project': prior == 0, 'workspace_bound': True}
            if latest != run.id or prior != 0:
                raise ValueError('Original first C run is no longer the only/latest generation')
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
            # Plan only after exact count proves this bounded list is complete.
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
                                       select(func.count()).select_from(Operation).where(*operation_scope, Operation.status.in_(('pending', 'waiting_capacity', 'running', 'indeterminate'))).scalar_subquery(),
                                       select(func.count()).select_from(Activity).where(Activity.workspace_id == workspace.id, unfinished_predicate(Activity.state, Activity.finished_at, ('completed','failed','timed_out','cancelled'))).scalar_subquery(),
                                       select(func.count()).select_from(Operation).where(Operation.workspace_id == workspace.id, unfinished_predicate(Operation.status, Operation.finished_at, ('completed','failed','cancelled'))).scalar_subquery()), 'exact_unresolved_counts')).one()
            result['exact_run_counts'] = dict(zip(('activity_total', 'active_activity', 'operation_total', 'active_or_indeterminate_operation', 'workspace_nonterminal_activity', 'workspace_nonterminal_operation'), counts))
            journal_ids = journal_plan(activities, result['exact_run_counts']['activity_total'], workspace.id, run.id)
            result['journal_coverage'] = dict(complete_exact_activity_list=True,
                expected_count=result['exact_run_counts']['activity_total'], planned_count=len(journal_ids),
                max_GETs=10, generation_run_sha256=sha(run.id), workspace_sha256=sha(workspace.id))
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
            baseline = (await read(select(Snapshot.id, Snapshot.project_id, Snapshot.commit_sha)
                                   .where(Snapshot.id == project.current_snapshot_id, Snapshot.project_id == project.id,
                                          uuid_hash(Snapshot.id) == SNAPSHOT_SHA).limit(2), 'original_snapshot')).one()
            version = (await read(select(ProjectVersion.id, ProjectVersion.snapshot_id, ProjectVersion.generation_run_id, ProjectVersion.status)
                                  .where(ProjectVersion.project_id == project.id, uuid_hash(ProjectVersion.id) == VERSION_SHA,
                                         ProjectVersion.snapshot_id == baseline.id, ProjectVersion.generation_run_id == run.id).limit(2), 'original_version')).one()
            if version.status != 'ready' or not re.fullmatch('[0-9a-f]{40}', baseline.commit_sha):
                raise ValueError('Original current READY snapshot/version metadata differs')
            RAW_IDS.update(snapshot_id=str(baseline.id), version_id=str(version.id))
            result['original_baseline'] = dict(current_snapshot_sha256=sha(baseline.id), version_sha256=sha(version.id),
                                               commit_sha=baseline.commit_sha, version_status='ready', exact_original_metadata=True,
                                               expected_source_sha256=SOURCE_SHA, full_source_re_read=False)
            settlements = (await read(select(UsageSettlement.status, func.count()).join(Usage, Usage.id == UsageSettlement.usage_id)
                                      .where(Usage.user_id == user.id, Usage.project_id == project.id, Usage.run_id == run.id,
                                             UsageSettlement.user_id == user.id).group_by(UsageSettlement.status), 'usage_settlement_counts')).all()
            result['settlements'] = [{'status': label(k, {'free','settled','unpaid'}), 'rows': n} for k,n in settlements]
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
                entry.update(exact_native_first_run_binding=True, state=label(j.state, {'running', 'completed', 'failed', 'timed_out', 'cancelled'}),
                             phase=label(j.phase, PHASES), log_bytes=j.log_bytes, heartbeat_at=stamp(j.heartbeat_at),
                             terminal_present=terminal is not None, terminal={'ok': terminal.ok, 'exit_code': terminal.exit_code,
                             'timed_out': terminal.timed_out, 'before_identity_present': terminal.before_identity is not None,
                             'after_identity_present': terminal.after_identity is not None, 'environment_mutated': terminal.environment_mutated,
                             'error': error(terminal.detail)} if terminal else None)
            except Exception as exc:
                entry['read_error'] = exception_metadata(exc)
            result['controller_journals'].append(entry)
        result['native_read_facts'] = native_facts(result)
        result['status'] = 'ACTUAL_OWN_C_FUTURE_NATIVE_READONLY'
        result['finished_utc'] = datetime.now(UTC).isoformat()
        result['acceptance_note'] = 'Observed metadata only; no completed/ready/quiescent/launch acceptance inferred. Unknown labels preserved by presence/hash.'
        return result
    finally:
        await dispose_engine()

def journal_plan(activities, count, workspace_id, run_id):
    if type(count) is not int or not 0 <= count <= 10 or count != len(activities):
        raise ValueError('Exact first-run activity list is incomplete or oversized')
    ids = [a.operation_id for a in activities]
    if len(set(ids)) != count or any(a.workspace_id != workspace_id or a.generation_run_id != run_id for a in activities):
        raise ValueError('Duplicate or foreign native journal binding')
    return ids


def complete_controller_coverage(result):
    coverage = result.get('journal_coverage', {})
    journals = result.get('controller_journals', [])
    count = coverage.get('expected_count')
    return (coverage.get('complete_exact_activity_list') is True and type(count) is int and 0 < count <= 10
            and coverage.get('planned_count') == count and len(journals) == count
            and all(not j.get('read_error') and j.get('exact_native_first_run_binding') is True
                    and j.get('state', {}).get('value') in {'completed','failed','timed_out','cancelled'}
                    and j.get('terminal_present') is True for j in journals))


def native_facts(result):
    counts = result['exact_run_counts']
    return dict(
        original_first_run_completed=result['exact_scope']['latest_run_same'] and result['exact_scope']['first_run_for_project']
            and result['run']['status']['value'] == 'completed' and result['run']['finished_at'] is not None,
        lease_absent_confirmed=result['workspace']['generation_lease_present'] is False,
        activities_all_terminal_confirmed=counts['workspace_nonterminal_activity'] == 0,
        operations_active_zero_confirmed=counts['workspace_nonterminal_operation'] == 0,
        controller_exact_run_terminal_confirmed=complete_controller_coverage(result)
            and result['run']['status']['value'] == 'completed' and result['run']['finished_at'] is not None
            and result['workspace']['generation_lease_present'] is False
            and counts['workspace_nonterminal_activity'] == 0 and counts['workspace_nonterminal_operation'] == 0,
        controller_terminal_note='True only for complete exact original-run native activity journal coverage; not successful-build or readiness acceptance.',
        original_snapshot_version_metadata_confirmed=result['original_baseline']['exact_original_metadata'],
        source_bytes_verified=False,
    )


def receipt_path(revision):
    if not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{40}', revision) or revision == OLD_RELEASE:
        raise ValueError('Future revision required')
    return '/tmp/qa-owned-c-native-' + revision + '.json'


def main():
    global RELEASE
    parser = argparse.ArgumentParser()
    parser.add_argument('--revision')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--scope-digest', action='store_true')
    args = parser.parse_args()
    if not args.execute and not args.scope_digest:
        print(json.dumps({'status': 'PREPARED_ONLY_NO_DB_NETWORK', 'purpose': PURPOSE, 'accepted': False}))
        return 0
    fd = None
    RAW_IDS.clear()
    PRIVATE_PROBE_OUTPUT.clear()
    try:
        if args.execute and args.scope_digest:
            raise ValueError('Mixed execution/digest modes forbidden')
        RELEASE = args.revision
        path = receipt_path(RELEASE)
        raw = sys.stdin.read(65537)
        if len(raw) > 65536:
            raise ValueError('Input oversized')
        data = json.loads(raw)
        if args.scope_digest and not args.execute:
            print(json.dumps({'status': 'SCOPE_DIGEST_ONLY_NO_DB_NETWORK', 'scope_sha256': scope(data)[1]}))
            return 0
        validate(data)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        result = asyncio.run(asyncio.wait_for(protected_probe(data), timeout=60))
    except Exception as exc:
        result = {'status': 'READONLY_C_FUTURE_NATIVE_BLOCKED', **exception_metadata(exc), 'accepted': False}
    if fd is not None:
        try:
            with os.fdopen(fd, 'w') as output:
                json.dump({'exact_scoped_identifiers_private': RAW_IDS, 'safe_summary': result,
                           'private_probe_output': PRIVATE_PROBE_OUTPUT}, output)
                output.flush()
                os.fsync(output.fileno())
            directory_fd = os.open('/tmp', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception as exc:
            result = {'status': 'READONLY_C_FUTURE_RECEIPT_WRITE_BLOCKED', **exception_metadata(exc), 'accepted': False}
    print(json.dumps(result))
    return 0 if result.get('status') == 'ACTUAL_OWN_C_FUTURE_NATIVE_READONLY' else 2


if __name__ == '__main__':
    raise SystemExit(main())
