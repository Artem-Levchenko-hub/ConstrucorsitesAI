"""Run inside yleum-prod-api. Owner-bound read-only metadata; never prints raw state/logs."""
import argparse
import asyncio
import json
from datetime import datetime
from uuid import UUID

PHASES = {'prepare', 'edit', 'fast_check', 'final_build', 'runtime_probe', 'snapshot', 'promote', 'complete'}
COUNTERS = {'bootstrap', 'fast_check', 'full_build', 'runtime_probe', 'proof_hit'}

def indicators(text):
    value = str(text or '').lower()
    terms = {'cancel': 'cancel', 'deadline': 'deadline', 'timeout': 'timeout',
             'provider': 'provider', 'infrastructure': 'infra', 'conflict': 'conflict',
             'source': 'source', 'build': 'build', 'permission': 'permission',
             'max_finalization_failed': 'max_finalization_failed'}
    return [label for label, needle in terms.items() if needle in value]

def public_state(raw):
    root = json.loads(raw) if isinstance(raw, str) else raw
    state = root.get('max_finalization', {}) if isinstance(root, dict) else {}
    state = state if isinstance(state, dict) else {}
    cp = state.get('checkpoint', {})
    cp = cp if isinstance(cp, dict) else {}
    result = {'present': bool(state), 'checkpoint_present': bool(cp)}
    for key in ('current_phase',):
        result[key] = state.get(key) if state.get(key) in PHASES else None
    result['checkpoint_phase'] = cp.get('phase') if cp.get('phase') in PHASES else None
    result['outcome'] = state.get('outcome') if state.get('outcome') in {'complete', 'completed', 'failed', 'cancelled', 'needs_edit', 'activating'} else None
    for key in ('started_at_ms', 'finished_at_ms', 'current_phase_started_at_ms'):
        result[key] = state.get(key) if type(state.get(key)) is int else None
    for key, permitted in (('phase_ms', PHASES), ('counters', COUNTERS)):
        values = state.get(key, {})
        result[key] = {k: v for k, v in values.items() if k in permitted and type(v) is int} if isinstance(values, dict) else {}
    for key in ('operation_id', 'candidate_id'):
        try:
            result['checkpoint_' + key] = str(UUID(str(cp[key])))
        except (ValueError, KeyError, TypeError):
            result['checkpoint_' + key] = None
    result['terminal_reason_indicators'] = indicators(state.get('terminal_reason'))
    return result

def serial(value):
    if isinstance(value, (UUID, datetime)):
        return str(value)
    raise TypeError('Unsupported public metadata type')

async def main(args):
    import asyncpg
    from yleum_api.core.config import get_settings
    project, owner = UUID(args.project_id), UUID(args.owner_id)
    explicit_run = UUID(args.run_id) if args.run_id else None
    dsn = get_settings().database_url.replace('postgresql+asyncpg://', 'postgresql://', 1)
    conn = await asyncpg.connect(dsn, timeout=15, command_timeout=15)
    try:
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            p = await conn.fetchrow('SELECT id, owner_id, current_snapshot_id FROM projects WHERE id=$1 AND owner_id=$2', project, owner)
            if p is None:
                raise PermissionError('Owner mismatch')
            r = await conn.fetchrow('''SELECT id,status,response_mode,execution_backend,created_at,started_at,
                execution_started_at,finished_at,agent_state,error FROM generation_runs
                WHERE project_id=$1 AND user_id=$2 AND ($3::uuid IS NULL OR id=$3)
                ORDER BY created_at DESC LIMIT 1''', project, owner, explicit_run)
            if r is None:
                raise LookupError('No exact owned run')
            run_id = r['id']
            result = {'project_id': project, 'run_id': run_id, 'readonly': True,
                'current_snapshot_id': p['current_snapshot_id'],
                'run': {k: r[k] for k in ('status','response_mode','execution_backend','created_at','started_at','execution_started_at','finished_at')},
                'error_present': bool(r['error']), 'error_indicators': indicators(r['error']),
                'coordinator': public_state(r['agent_state'])}
            w = await conn.fetchrow('''SELECT id,state,orchestrator,generation_run_id,fencing_epoch,
                created_at,updated_at,ready_at,last_error FROM project_cell_workspaces
                WHERE project_id=$1 AND owner_id=$2''', project, owner)
            result['workspace'] = {k: w[k] for k in ('id','state','orchestrator','generation_run_id','fencing_epoch','created_at','updated_at','ready_at')} if w else None
            if w:
                result['workspace']['error_present'] = bool(w['last_error'])
                result['workspace']['error_indicators'] = indicators(w['last_error'])
                result['owned_container_name_prefixes'] = ['omnia-machine-' + w['id'].hex, 'omnia-cell-' + w['id'].hex]
                result['owned_container_label_filter'] = 'omnia.workspace_id=' + str(w['id'])
            result['snapshots'] = dict(await conn.fetchrow('''SELECT count(*) AS total,
                count(*) FILTER (WHERE parent_id IS NOT NULL) AS non_initial,
                max(created_at) AS latest_at FROM snapshots WHERE project_id=$1''', project))
            result['proof_dimensions'] = [dict(row) for row in await conn.fetch('''SELECT pr.dimension,pr.outcome,
                count(*) AS count,min(pr.created_at) AS first_at,max(pr.created_at) AS last_at
                FROM project_cell_proof_results pr JOIN project_cell_proofs pp ON pp.id=pr.proof_id
                JOIN project_cell_workspaces w ON w.id=pp.workspace_id
                WHERE pp.generation_run_id=$1 AND w.project_id=$2 AND w.owner_id=$3
                GROUP BY pr.dimension,pr.outcome ORDER BY pr.dimension,pr.outcome''', run_id, project, owner)]
            result['candidates'] = [dict(row) for row in await conn.fetch('''SELECT c.status,c.cancelled,
                count(*) AS count,min(c.created_at) AS first_at,max(c.promoted_at) AS last_promoted_at
                FROM project_cell_candidates c JOIN project_cell_workspaces w ON w.id=c.workspace_id
                WHERE c.generation_run_id=$1 AND w.project_id=$2 AND w.owner_id=$3
                GROUP BY c.status,c.cancelled ORDER BY c.status''', run_id, project, owner)]
            result['activities'] = [dict(row) for row in await conn.fetch('''SELECT a.operation_id,a.kind,a.state,
                CASE WHEN a.phase IN ('prepare','edit','fast_check','final_build','runtime_probe','snapshot','promote','complete') THEN a.phase ELSE NULL END AS phase,
                a.started_at,a.deadline_at,a.heartbeat_at,a.finished_at,a.log_bytes
                FROM project_cell_activity_leases a JOIN project_cell_workspaces w ON w.id=a.workspace_id
                WHERE a.generation_run_id=$1 AND w.project_id=$2 AND w.owner_id=$3
                ORDER BY a.started_at DESC LIMIT 15''', run_id, project, owner)]
            result['operations'] = [dict(row) for row in await conn.fetch('''SELECT o.id,o.kind,o.status,o.attempt_count,
                o.created_at,o.started_at,o.finished_at,(o.error IS NOT NULL) AS error_present
                FROM project_cell_operations o JOIN project_cell_workspaces w ON w.id=o.workspace_id
                WHERE o.generation_run_id=$1 AND w.project_id=$2 AND w.owner_id=$3
                ORDER BY o.created_at DESC LIMIT 15''', run_id, project, owner)]
            result['phase_events'] = [dict(row) for row in await conn.fetch('''SELECT seq,created_at,
                CASE WHEN payload->>'phase' IN ('prepare','edit','fast_check','final_build','runtime_probe','snapshot','promote','complete')
                     THEN payload->>'phase' ELSE NULL END AS phase
                FROM generation_events WHERE generation_run_id=$1 AND project_id=$2 AND event_type='generation.phase'
                ORDER BY seq DESC LIMIT 20''', run_id, project)]
            result['native_boundary_events'] = [dict(row) for row in await conn.fetch('''SELECT seq,created_at,payload->>'kind' AS kind
                FROM generation_events WHERE generation_run_id=$1 AND project_id=$2 AND event_type='agent.step'
                AND payload->>'kind' IN ('done','stalled') ORDER BY seq''', run_id, project)]
            result['usage_summary'] = dict(await conn.fetchrow('''SELECT count(*) AS rows,
                coalesce(sum(tokens_in),0) AS tokens_in,coalesce(sum(tokens_out),0) AS tokens_out,
                min(created_at) AS first_at,max(created_at) AS last_at
                FROM usage WHERE run_id=$1 AND project_id=$2 AND user_id=$3''', run_id, project, owner))
            print(json.dumps(result, default=serial, ensure_ascii=True, indent=2))
    finally:
        await conn.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('project_id')
    parser.add_argument('owner_id')
    parser.add_argument('--run-id')
    args = parser.parse_args()
    try:
        asyncio.run(main(args))
    except Exception as exc:
        # No exception message/traceback: database errors can contain DSNs or payloads.
        print(json.dumps({'status':'BLOCKED','error_type':type(exc).__name__}))
        raise SystemExit(1)
