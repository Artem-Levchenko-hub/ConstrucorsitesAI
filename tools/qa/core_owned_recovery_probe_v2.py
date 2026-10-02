"""Inside old-live API: owner-bound DB reads and existing controller GETs only."""
import argparse
import asyncio
import hashlib
import json
import re
from datetime import datetime
from uuid import UUID

WORKSPACE = UUID('55bdcfc4-e011-44f0-9b79-1a9f7acaa9c8')
ACTIVITY = UUID('dfd1b513-bbe8-5804-9a5c-dbbf00b34f5f')
RELEASE = UUID('b466bc13-e77d-47a3-b6dd-7fbd2191abf6')
CODES = {'cancelled_after_dispatch', 'cancelled_after_claim', 'cancelled_after_control_claim',
         'invalid_response_object', 'OrchestratorUnavailable', 'ReadTimeout', 'ConnectError',
         'ConnectTimeout', 'RemoteProtocolError', 'TimeoutError', 'CancelledError'}
STATES = {'ready', 'retained', 'resources_ready', 'resources_paused', 'partial', 'degraded',
          'deleted', 'deleting', 'running', 'completed', 'failed', 'timed_out', 'cancelled',
          'active', 'indeterminate', 'pending', 'waiting_capacity', 'absent', 'stopped'}

def safe_error(value):
    # Project Cell errors are hashed by _hashed_error; never try to print raw text.
    text = str(value or '')
    matched = next((code for code in sorted(CODES) if text == code or
        text == 'provider_error:' + hashlib.sha256(code.encode()).hexdigest()), None)
    return {'present': bool(text), 'hashed': bool(re.fullmatch(r'provider_error:[0-9a-f]{64}', text)),
            'known_code': matched}

def serial(value):
    if isinstance(value, (UUID, datetime)):
        return str(value)
    raise TypeError('Unsupported metadata')

def failure(exc):
    return {'error_type': type(exc).__name__, 'status_code': getattr(exc, 'status_code', None)}

async def main(args):
    import asyncpg
    from yleum_api.core.config import get_settings
    from yleum_api.services.orchestrator_client import (
        project_cell_agent_operation_status, _request, ProjectCellResourceResponse)
    owner, project, run = UUID(args.owner), UUID(args.project), UUID(args.run)
    dsn = get_settings().database_url.replace('postgresql+asyncpg://', 'postgresql://', 1)
    connection = await asyncpg.connect(dsn, timeout=10, command_timeout=10)
    try:
        async with connection.transaction(readonly=True, isolation='repeatable_read'):
            w = await connection.fetchrow('''SELECT w.id,w.project_id,w.owner_id,w.generation_run_id,
                w.orchestrator,w.fencing_epoch,w.state FROM project_cell_workspaces w
                JOIN projects p ON p.id=w.project_id JOIN generation_runs r ON r.id=w.generation_run_id
                WHERE w.id=$1 AND w.project_id=$2 AND w.owner_id=$3 AND p.owner_id=$3
                AND w.generation_run_id=$4 AND r.project_id=$2 AND r.user_id=$3 AND r.status='cancelled' ''',
                WORKSPACE, project, owner, run)
            if w is None or w['orchestrator'] != 'commerce' or w['fencing_epoch'] != 2:
                raise PermissionError('Exact cancelled OWN-A workspace changed')
            a = await connection.fetchrow('''SELECT operation_id,workspace_id,generation_run_id,
                fencing_epoch,state,started_at,deadline_at,heartbeat_at,finished_at,log_bytes
                FROM project_cell_activity_leases WHERE operation_id=$1 AND workspace_id=$2
                AND generation_run_id=$3''', ACTIVITY, WORKSPACE, run)
            o = await connection.fetchrow('''SELECT id,workspace_id,generation_run_id,kind,status,
                fencing_epoch,created_at,started_at,finished_at,error FROM project_cell_operations
                WHERE id=$1 AND workspace_id=$2 AND generation_run_id=$3 AND kind='release' ''',
                RELEASE, WORKSPACE, run)
            if a is None or o is None:
                raise PermissionError('Exact OWN-A receipts absent')
            result = {'readonly': True, 'workspace': dict(w), 'activity': dict(a),
                      'release': {k: v for k, v in dict(o).items() if k != 'error'},
                      'release_error': safe_error(o['error'])}
        # Both endpoints are existing read-only GETs, without a new epoch or command.
        try:
            s = await project_cell_agent_operation_status(WORKSPACE, ACTIVITY)
            if s.operation_id != ACTIVITY:
                raise PermissionError('Journal operation mismatch')
            t = s.terminal_response
            result['journal'] = {'operation_id': s.operation_id, 'state': s.state,
                'phase': s.phase if s.phase in {'prepare','edit','fast_check','final_build','full_build','build'} else None,
                'started_at': s.started_at, 'deadline_at': s.deadline_at,
                'heartbeat_at': s.heartbeat_at, 'log_bytes': s.log_bytes,
                'terminal_present': t is not None,
                'terminal': {'operation_id': t.operation_id, 'ok': t.ok, 'exit_code': t.exit_code,
                    'timed_out': t.timed_out, 'identity_present': t.before_identity is not None and t.after_identity is not None,
                    'environment_mutated': t.environment_mutated,
                    'detail_error': safe_error(t.detail)} if t else None}
        except Exception as exc:
            result['journal_failure'] = failure(exc)
        try:
            raw = await _request('GET', f'/internal/workspaces/{WORKSPACE}/resources')
            r = ProjectCellResourceResponse.from_json(raw)
            if r.workspace_id != WORKSPACE:
                raise PermissionError('Resource workspace mismatch')
            result['resources'] = {'workspace_id': r.workspace_id, 'fencing_epoch': r.fencing_epoch,
                'state': r.state if r.state in STATES else None,
                **{key: getattr(r, key) for key in ('has_workspace','has_agent_home','has_postgres','has_redis','has_draft_runtime')},
                'checkpoint_present': bool(r.checkpoint_ref)}
        except Exception as exc:
            result['resources_failure'] = failure(exc)
        print(json.dumps(result, default=serial))
    finally:
        await connection.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('project')
    parser.add_argument('owner')
    parser.add_argument('run')
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception as exc:
        print(json.dumps({'blocked': True, **failure(exc)}))
        raise SystemExit(1) from None
