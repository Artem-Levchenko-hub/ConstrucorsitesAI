"""Prepared-only owned C post-edit quiescence observation; no commands or models."""
import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import logging
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

PURPOSE = 'OWN_C_POST_SOURCE_FIX_TYPED_NATIVE_READONLY'
FIXED = {
    'owner_id_sha256': 'f2ef40a428c9e57e4c215d8d6c0a55871d952ca201e6c3edb1be9f50dadbd066',
    'project_id_sha256': '9f9147a8ba0b192858dee0aae6650b8b491f504949603883150ebb552d67cda1',
    'workspace_id_sha256': '43a24989fdbb7fa1d6845eb6d732894de5a0c366630226b57f9372445fe5e24d',
}
OLD_RUN = 'e44f8ac5d1a5cf31a6ef3164fecc3e4fe18e5027ccaa4aff7c989774c04af915'
PAIRS = {'command': {'prepare', 'fast_check', 'final_build'}, 'tool': {'runtime_probe'},
         'snapshot': {'snapshot'}, 'promotion': {'promote'}}
TERMINAL = {'completed', 'failed', 'timed_out', 'cancelled'}
RAW_IDS = {}
PRIVATE_OUTPUT = {}
PHASE = 'input'
MAX_SQL = 9
MAX_ACTIVITIES = 10


def sha(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def helper_sha():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def hex_string(value, length):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{' + str(length) + '}', value) is not None


def scope(data):
    if not hex_string(data.get('revision'), 40) or not hex_string(data.get('api_container_id'), 64):
        raise ValueError('Actual delivered revision/container required')
    if data.get('fixture') != {'fixture_label': 'C', 'owner_role': 'user', **FIXED}:
        raise ValueError('Exact owned C identity required')
    run = data.get('new_run_sha256')
    if not hex_string(run, 64) or run == OLD_RUN or not hex_string(data.get('source_fix_safe_receipt_sha256'), 64):
        raise ValueError('Actual new source-fix run/receipt hashes required')
    binding = {'purpose': PURPOSE, 'revision': data['revision'], 'api_container_id': data['api_container_id'],
               'helper_sha256': helper_sha(), 'selected_host': 'commerce', 'fixture': data['fixture'],
               'new_run_sha256': run, 'source_fix_safe_receipt_sha256': data['source_fix_safe_receipt_sha256'],
               'max_sql': MAX_SQL, 'max_activities': MAX_ACTIVITIES, 'max_native_command_GETs': MAX_ACTIVITIES,
               'coverage': 'typed_COMMAND_native_others_local_DB'}
    return sha(json.dumps(binding, sort_keys=True, separators=(',', ':')))


def validate(data):
    digest = scope(data)
    ack = data['read_ack']
    issued = datetime.fromisoformat(ack['issued_at_utc'].replace('Z', '+00:00'))
    if issued.tzinfo is None or not 0 <= (datetime.now(UTC) - issued).total_seconds() <= 300:
        raise ValueError('Fresh root READ ACK required')
    if (ack.get('purpose') != PURPOSE or ack.get('scope_sha256') != digest or ack.get('helper_sha256') != helper_sha()
            or ack.get('root_readonly_authorized') is not True or ack.get('production_delivered') is not True
            or ack.get('exclusive_actor') is not True or ack.get('inprogress') is not False
            or ack.get('model_calls_authorized') is not False or ack.get('new_commands_authorized') is not False):
        raise ValueError('Exact root readonly authorization required')
    return digest


def unfinished(state, finished, terminal):
    from sqlalchemy import or_
    return or_(state.notin_(terminal), finished.is_(None))


def typed_plan(activities, count, workspace_id, run_id):
    if type(count) is not int or not 0 < count <= MAX_ACTIVITIES or len(activities) != count:
        raise ValueError('Complete bounded activity list required')
    ids = [a.operation_id for a in activities]
    if len(set(ids)) != count:
        raise ValueError('Unique exact activities required')
    commands, local = [], []
    for activity in activities:
        if (activity.workspace_id != workspace_id or activity.generation_run_id != run_id
                or activity.kind not in PAIRS or activity.phase not in PAIRS[activity.kind]
                or activity.state not in TERMINAL or activity.finished_at is None):
            raise ValueError('Unknown, nonterminal or foreign typed activity')
        (commands if activity.kind == 'command' else local).append(activity)
    if not commands:
        raise ValueError('At least one actual native COMMAND required')
    return commands, local


def journal_fact(journal, operation_id):
    terminal = journal.terminal_response
    if (journal.operation_id != operation_id or journal.state not in TERMINAL or terminal is None
            or terminal.operation_id != operation_id):
        raise ValueError('Exact terminal native journal required')
    return dict(operation_sha256=sha(operation_id), exact_native_command_binding=True,
                terminal_state=journal.state, terminal_response_present=True,
                ok=terminal.ok, exit_code=terminal.exit_code, timed_out=terminal.timed_out,
                error_present=bool(terminal.detail), error_sha256=sha(terminal.detail) if terminal.detail else None)


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
    stdout, stderr = BoundedCapture(), BoundedCapture()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            import structlog
            structlog.configure(logger_factory=structlog.ReturnLoggerFactory())
            return await probe(data)
    finally:
        PRIVATE_OUTPUT.update(stdout=stdout.getvalue(), stderr=stderr.getvalue(),
                              stdout_bytes=stdout.used, stderr_bytes=stderr.used)


async def probe(data):
    global PHASE
    digest = validate(data)
    logging.disable(logging.CRITICAL)
    from sqlalchemy import Text, cast, func, select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from yleum_api.core.config import get_settings
    from yleum_api.core.db import get_engine, dispose_engine
    from yleum_api.models.user import User
    from yleum_api.models.project import Project
    from yleum_api.models.generation_run import GenerationRun as Run
    from yleum_api.models.project_cell import ProjectCellWorkspace as Workspace
    from yleum_api.models.project_cell import ProjectCellActivityLease as Activity
    from yleum_api.models.project_cell import ProjectCellOperation as Operation
    from yleum_api.services import orchestrator_hosts as hosts
    from yleum_api.services import orchestrator_client as sdk
    PHASE = 'revision'
    if get_settings().omnia_release_sha != data['revision']:
        raise ValueError('Actual API revision differs')
    result = dict(revision=data['revision'], scope_sha256=digest, scope_id_hashes=FIXED,
                  new_run_sha256=data['new_run_sha256'], api_container_id_from_root_scope=data['api_container_id'],
                  api_container_runtime_identity_verified=False, accepted=False, source_bytes_verified=False,
                  effective_version_ready_confirmed=False, effective_version_current_confirmed=False,
                  model_calls=0, new_commands=0, max_sql=MAX_SQL, sql_statements=0, native_command_journals=[])
    result['max_native_command_GETs'] = MAX_ACTIVITIES
    uuid_hash = lambda column: func.encode(func.sha256(func.convert_to(cast(column, Text), 'UTF8')), 'hex')
    try:
        async with async_sessionmaker(get_engine(), expire_on_commit=False)() as session:
            async def read(statement, phase):
                global PHASE
                PHASE = phase
                result['sql_statements'] += 1
                if result['sql_statements'] > MAX_SQL:
                    raise ValueError('SQL budget exceeded')
                return await session.execute(statement)
            await read(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'), 'readonly')
            await read(text("SET LOCAL statement_timeout='5000ms'"), 'timeout')
            user = (await read(select(User.id).where(uuid_hash(User.id) == FIXED['owner_id_sha256'], User.role == 'user').limit(2), 'owner')).one()
            project = (await read(select(Project.id).where(uuid_hash(Project.id) == FIXED['project_id_sha256'], Project.owner_id == user.id).limit(2), 'project')).one()
            workspace = (await read(select(Workspace.id, Workspace.owner_id, Workspace.project_id, Workspace.provider, Workspace.orchestrator,
                                          Workspace.generation_run_id, Workspace.fencing_epoch).where(
                uuid_hash(Workspace.id) == FIXED['workspace_id_sha256'], Workspace.owner_id == user.id,
                Workspace.project_id == project.id, Workspace.deleted_at.is_(None)).limit(2), 'workspace')).one()
            run = (await read(select(Run.id, Run.project_id, Run.user_id, Run.status, Run.response_mode, Run.finished_at).where(
                uuid_hash(Run.id) == data['new_run_sha256'], Run.project_id == project.id, Run.user_id == user.id).limit(2), 'new_edit')).one()
            latest = (await read(select(Run.id).where(Run.project_id == project.id).order_by(Run.created_at.desc(), Run.id.desc()).limit(1), 'latest')).scalar_one()
            if (run.id != latest or run.status != 'completed' or run.response_mode != 'edit' or run.finished_at is None
                    or run.project_id != project.id or run.user_id != user.id
                    or workspace.project_id != project.id or workspace.owner_id != user.id
                    or workspace.provider != 'docker_owner_canary' or workspace.orchestrator != 'commerce'
                    or type(workspace.fencing_epoch) is not int or workspace.fencing_epoch <= 0):
                raise ValueError('Exact latest completed owned edit required')
            reg = hosts.registry()
            if reg.single is not False or reg.get(workspace.orchestrator).name != 'commerce':
                raise ValueError('Normal Commerce routing differs')
            hosts.remember_workspace_host(workspace.id, project.id, 'commerce')
            RAW_IDS.update(owner_id=str(user.id), project_id=str(project.id), workspace_id=str(workspace.id), run_id=str(run.id))
            if any(sha(RAW_IDS[k]) != FIXED[k + '_sha256'] for k in ('owner_id', 'project_id', 'workspace_id')) or sha(run.id) != data['new_run_sha256']:
                raise ValueError('Actual owned UUID binding differs')
            counts = (await read(select(
                select(func.count()).select_from(Activity).where(Activity.workspace_id == workspace.id, Activity.generation_run_id == run.id).scalar_subquery(),
                select(func.count()).select_from(Activity).where(Activity.workspace_id == workspace.id, unfinished(Activity.state, Activity.finished_at, tuple(TERMINAL))).scalar_subquery(),
                select(func.count()).select_from(Operation).where(Operation.workspace_id == workspace.id, unfinished(Operation.status, Operation.finished_at, ('completed', 'failed', 'cancelled'))).scalar_subquery()), 'workspace_unresolved')).one()
            activities = (await read(select(Activity).where(Activity.workspace_id == workspace.id, Activity.generation_run_id == run.id)
                                     .order_by(Activity.started_at, Activity.operation_id).limit(MAX_ACTIVITIES + 1), 'all_typed_activities')).scalars().all()
            if workspace.generation_run_id is not None or counts[1] != 0 or counts[2] != 0:
                raise ValueError('Workspace lease or unresolved work remains')
            commands, local = typed_plan(activities, counts[0], workspace.id, run.id)
            result.update(captured_utc=datetime.now(UTC).isoformat(), new_run_completed_edit_latest=True,
                          workspace_fencing_epoch=workspace.fencing_epoch, workspace_lease_absent=True,
                          workspace_unresolved_activity=counts[1], workspace_unresolved_operation=counts[2],
                          typed_activity_count=counts[0], native_command_count=len(commands), local_db_activity_count=len(local),
                          local_db_activities=[dict(operation_sha256=sha(a.operation_id), kind=a.kind, phase=a.phase,
                                                    terminal_state=a.state, finished_at=a.finished_at.isoformat()) for a in local])
        RAW_IDS['native_command_operation_ids'] = [str(a.operation_id) for a in commands]
        PHASE = 'existing_native_COMMAND_GET'
        for activity in commands:
            journal = await asyncio.wait_for(sdk.project_cell_agent_operation_status(workspace.id, activity.operation_id), timeout=8)
            result['native_command_journals'].append(journal_fact(journal, activity.operation_id))
        result.update(status='ACTUAL_OWN_C_POST_EDIT_TYPED_NATIVE_OBSERVATION',
                      typed_quiescence_confirmed=True, finished_utc=datetime.now(UTC).isoformat(),
                      acceptance_note='Typed terminal observation only; no successful-build, effective version, full source, restore or launch acceptance.')
        return result
    finally:
        await dispose_engine()


def receipt_path(data):
    scope(data)
    return '/tmp/qa-owned-c-post-source-fix-native-' + data['new_run_sha256'] + '.json'


def write_receipt(fd, result):
    with os.fdopen(fd, 'w') as output:
        json.dump(dict(exact_scoped_identifiers_private=RAW_IDS, safe_summary=result, private_probe_output=PRIVATE_OUTPUT), output)
        output.flush()
        os.fsync(output.fileno())
    parent = os.open('/tmp', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def reserve_receipt(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.fsync(fd)
        parent = os.open('/tmp', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    except Exception:
        os.close(fd)
        raise
    return fd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--scope-digest', action='store_true')
    args = parser.parse_args()
    if not args.execute and not args.scope_digest:
        print(json.dumps(dict(status='PREPARED_ONLY_NO_DB_NETWORK', purpose=PURPOSE, accepted=False)))
        return 0
    fd = None
    RAW_IDS.clear()
    PRIVATE_OUTPUT.clear()
    try:
        if args.execute and args.scope_digest:
            raise ValueError('Mixed modes forbidden')
        raw = sys.stdin.read(65537)
        if len(raw) > 65536:
            raise ValueError('Input oversized')
        data = json.loads(raw)
        if args.scope_digest:
            print(json.dumps(dict(status='SCOPE_DIGEST_ONLY_NO_DB_NETWORK', scope_sha256=scope(data))))
            return 0
        validate(data)
        fd = reserve_receipt(receipt_path(data))
        validate(data)  # Recheck actual ACK after durable attempt reservation.
        result = asyncio.run(asyncio.wait_for(protected_probe(data), timeout=60))
    except Exception as exc:
        result = dict(status='OWN_C_POST_EDIT_NATIVE_BLOCKED', failure_phase=PHASE,
                      error_type=type(exc).__name__, accepted=False, typed_quiescence_confirmed=False)
    if fd is not None:
        try:
            write_receipt(fd, result)
        except Exception as exc:
            result = dict(status='OWN_C_POST_EDIT_RECEIPT_WRITE_BLOCKED', error_type=type(exc).__name__,
                          accepted=False, typed_quiescence_confirmed=False)
    print(json.dumps(result))
    return 0 if result.get('status') == 'ACTUAL_OWN_C_POST_EDIT_TYPED_NATIVE_OBSERVATION' else 2


if __name__ == '__main__':
    raise SystemExit(main())
