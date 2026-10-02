"""OWN C resources only, prepare-only. Fresh root ACK gates one resources GET.
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
import stat
from urllib.parse import urlsplit
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

RELEASE = 'dd91e82556d50e7e5308501cdf76a08235babed5'
PROJECT_SHA = '9f9147a8ba0b192858dee0aae6650b8b491f504949603883150ebb552d67cda1'
RUN_SHA = 'e44f8ac5d1a5cf31a6ef3164fecc3e4fe18e5027ccaa4aff7c989774c04af915'
PURPOSE = 'OWN_C_READONLY_RESOURCES_DIAGNOSTIC'
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
    hashes = {k: f[k] for k in ('owner_id_sha256', 'project_id_sha256', 'run_id_sha256')}
    if not all(isinstance(v, str) and re.fullmatch(r'[0-9a-f]{64}', v) for v in hashes.values()):
        raise ValueError('Exact canonical UUID hash scope required')
    if hashes['project_id_sha256'] != PROJECT_SHA or hashes['run_id_sha256'] != RUN_SHA:
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

RESOURCE_STATES = {'resources_ready', 'resources_paused', 'retained', 'resources_missing',
                   'not_found', 'absent', 'failed', 'destroyed'}

def prior_scope(data):
    hashes, digest = validate(data)
    fd = os.open('/tmp/qa-owned-c-native1.json', os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 65536:
            raise ValueError('Prior scoped receipt must be protected regular file')
        prior = json.load(source)
    safe = prior['safe_summary']
    if safe.get('status') != 'ACTUAL_OWN_C_READONLY_DIAGNOSTIC' or safe.get('revision') != RELEASE or safe.get('scope_id_hashes') != hashes:
        raise ValueError('Prior frozen C proof scope differs')
    raw = prior['exact_scoped_identifiers_private']
    ids = {k: UUID(raw[k]) for k in ('owner_id', 'project_id', 'run_id', 'workspace_id')}
    if any(sha(ids[k]) != hashes[k + '_sha256'] for k in ('owner_id', 'project_id', 'run_id')):
        raise ValueError('Prior canonical UUID hashes differ')
    if sha(ids['workspace_id']) != safe['workspace']['id_sha256']:
        raise ValueError('Prior authoritative workspace hash differs')
    return ids, hashes, digest

def safe_raw_resource(payload, workspace_id):
    expected = {'workspace_id', 'state', 'provider_ref', 'fencing_epoch', 'checkpoint_ref',
                'has_workspace', 'has_agent_home', 'has_postgres', 'has_redis',
                'has_draft_runtime', 'draft_state', 'preview_url'}
    if not isinstance(payload, dict) or set(payload) - expected or payload.get('workspace_id') != str(workspace_id):
        return False
    if payload.get('state') not in RESOURCE_STATES:
        return False
    for key in ('provider_ref', 'checkpoint_ref'):
        value = payload.get(key)
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,255}', value)):
            return False
    value = payload.get('preview_url')
    if value:
        url = urlsplit(value)
        if (url.scheme != 'https' or url.username is not None or url.password is not None or
                url.query or url.fragment or not url.hostname or
                not url.hostname.startswith('cell-' + workspace_id.hex[:12] + '-dev.')):
            return False
    return True

async def probe(data):
    global FAILURE_PHASE
    ids, hashes, digest = prior_scope(data)
    logging.disable(logging.CRITICAL)
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from yleum_api.core.config import get_settings
    from yleum_api.core.db import get_engine, dispose_engine
    from yleum_api.models.user import User
    from yleum_api.models.project import Project
    from yleum_api.models.generation_run import GenerationRun as Run
    from yleum_api.models.project_cell import ProjectCellWorkspace as Workspace
    from yleum_api.services.project_cell_runtime import _get_cell_resources
    from yleum_api.services import orchestrator_client as sdk
    FAILURE_PHASE = 'revision'
    if get_settings().omnia_release_sha != RELEASE:
        raise ValueError('Actual API revision differs')
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    result = {'captured_utc': datetime.now(UTC).isoformat(), 'revision': RELEASE, 'scope_sha256': digest,
              'scope_id_hashes': hashes, 'readonly': True, 'model_calls': 0, 'new_commands': 0,
              'lifecycle_actions': 0, 'accepted': False, 'normal_resources_GET_count': 0}
    raw = None
    try:
        async with factory() as session:
            FAILURE_PHASE = 'readonly_transaction'
            await session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            await session.execute(text("SET LOCAL statement_timeout='5000ms'"))
            FAILURE_PHASE = 'fresh_exact_identity'
            row = (await session.execute(select(Workspace.id, Workspace.fencing_epoch, Workspace.state,
                                                 Workspace.generation_run_id, Run.status.label('run_status'))
                    .select_from(User).join(Project, Project.owner_id == User.id)
                    .join(Run, (Run.project_id == Project.id) & (Run.user_id == User.id))
                    .join(Workspace, (Workspace.project_id == Project.id) & (Workspace.owner_id == User.id))
                    .where(User.id == ids['owner_id'], User.role == 'user', Project.id == ids['project_id'],
                           Run.id == ids['run_id'], Workspace.id == ids['workspace_id'],
                           Workspace.provider == 'docker_owner_canary', Workspace.deleted_at.is_(None)).limit(2))).one()
            result['database_identity'] = {'workspace_sha256': sha(row.id), 'owned_user_project_run_workspace_match': True,
                                          'fencing_epoch': row.fencing_epoch, 'state': label(row.state, {'provisioning', 'ready', 'stopped', 'failed', 'deleting', 'deleted'}),
                                          'run_status': label(row.run_status, {'pending', 'queued_for_capacity', 'running', 'cancel_requested', 'completed', 'failed', 'cancelled'}),
                                          'generation_lease_present': row.generation_run_id is not None,
                                          'lease_is_exact_run': row.generation_run_id == ids['run_id']}
        RAW_IDS.update({k: str(v) for k, v in ids.items()})
        FAILURE_PHASE = 'one_normal_resources_GET'
        original = sdk._request
        async def capture(method, path, **kwargs):
            nonlocal raw
            if method != 'GET' or path != f'/internal/workspaces/{ids["workspace_id"]}/resources' or result['normal_resources_GET_count'] != 0:
                raise ValueError('Only one exact existing resources GET allowed')
            result['normal_resources_GET_count'] += 1
            payload = await original(method, path, **kwargs)
            raw = payload
            return payload
        sdk._request = capture
        try:
            resource = await asyncio.wait_for(_get_cell_resources(ids['workspace_id']), timeout=10)
        finally:
            sdk._request = original
        if resource.workspace_id != ids['workspace_id'] or result['normal_resources_GET_count'] != 1:
            raise ValueError('Resource exact workspace identity differs')
        result['resources'] = {'workspace_identity_match': True, 'fencing_epoch': resource.fencing_epoch,
                               'fencing_epoch_matches_DB': resource.fencing_epoch == row.fencing_epoch,
                               'state': label(resource.state, RESOURCE_STATES), 'draft_state': label(resource.draft_state, {'running', 'stopped', 'failed'}),
                               'has_workspace': resource.has_workspace, 'has_agent_home': resource.has_agent_home,
                               'has_postgres': resource.has_postgres, 'has_redis': resource.has_redis,
                               'has_draft_runtime': resource.has_draft_runtime, 'preview_URL_present': bool(resource.preview_url),
                               'provider_ref_present': resource.provider_ref is not None, 'checkpoint_ref_present': resource.checkpoint_ref is not None}
        full_safe = safe_raw_resource(raw, ids['workspace_id'])
        result['private_full_resource_metadata_stored'] = full_safe
        result['status'] = 'ACTUAL_OWN_C_READONLY_RESOURCES'
        result['acceptance_note'] = 'Observed resources only; no completed-source means running inference, recovery or quiescence acceptance.'
        result['finished_utc'] = datetime.now(UTC).isoformat()
        return result, raw if full_safe else None
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
    raw = None
    try:
        data = json.loads(sys.stdin.read(65537))
        if args.scope_digest and not args.execute:
            print(json.dumps({'status': 'SCOPE_DIGEST_ONLY_NO_DB_NETWORK', 'scope_sha256': scope(data)[1]}))
            return
        validate(data)
        if not args.raw_output or not re.fullmatch(r'/(?:dev/shm|tmp)/qa-owned-c-resources-[A-Za-z0-9_-]{1,80}\.json', args.raw_output):
            raise ValueError('New protected C resources receipt path required')
        fd = os.open(args.raw_output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        result, raw = asyncio.run(asyncio.wait_for(probe(data), timeout=30))
    except Exception as exc:
        result = {'status': 'READONLY_C_RESOURCES_BLOCKED', **exception_metadata(exc), 'accepted': False}
    if fd is not None:
        try:
            with os.fdopen(fd, 'w') as output:
                json.dump({'exact_scoped_identifiers_private': RAW_IDS, 'resource_response_private_raw': raw, 'safe_summary': result}, output)
        except Exception as exc:
            result = {'status': 'READONLY_C_RESOURCES_RECEIPT_WRITE_BLOCKED', **exception_metadata(exc), 'accepted': False}
    print(json.dumps(result))
    sys.exit(0 if result.get('status') == 'ACTUAL_OWN_C_READONLY_RESOURCES' else 2)

if __name__ == '__main__':
    main()
