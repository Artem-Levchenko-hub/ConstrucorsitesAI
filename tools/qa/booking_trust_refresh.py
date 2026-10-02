"""Root-only ONE normal targeted SDK pause; normal owned Studio performs wake.

Prepare only. Execute inside the final API container after an explicit final
release ACK and fresh OWN B protected preflight receipt. Never calls a model,
Docker, a config patch, lease repair or app business mutation.
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

EXPECTED_RELEASE = '8fe8cff8bcd004dd61845d9b7d019114b3ae7707'
SDK_PATHS = ('src/lib/omnia/integration-client.ts', 'src/lib/omnia/max-config.ts',
             'src/components/MaxAppProvider.tsx')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def validate_inputs(fixture, ack, receipt, revision):
    if revision != EXPECTED_RELEASE or ack.get('sha') != revision:
        raise ValueError('Only the exact final reviewed8fe8 release is authorized')
    if not (fixture.get('fixture_label') == 'B' and fixture.get('owner_role') == 'user'
            and ack.get('scope') == 'one_owned_B_quiescent_pause_wake'
            and all(ack.get(key) is True for key in ('production_delivered','admission_reopened',
                'root_authorized','controller_revision_verified','exclusive_qa_account_confirmed',
                'no_competing_actor_confirmed'))):
        raise ValueError('Explicit final-release exclusive OWN B pause/wake ACK is required')
    keys = ('owner_id','project_id','workspace_id','terminal_run_id','accepted_snapshot_id','accepted_version_id')
    for key in keys:
        UUID(fixture[key])
        if receipt.get(key) != fixture[key]:
            raise ValueError('Exact owned receipt identity differs')
    if any(ack.get(key) != fixture[key] for key in ('owner_id','project_id','workspace_id')):
        raise ValueError('Root scope differs from the exact owned target')
    observed = datetime.fromisoformat(receipt['captured_utc'].replace('Z','+00:00'))
    if observed.tzinfo is None or not 0 <= (datetime.now(UTC)-observed).total_seconds() <= 300:
        raise ValueError('Actual owned baseline/records receipt must be fresh within300seconds')
    for key in ('own_identity_verified','ready_current_version_verified','source_read_verified',
                'sdk_read_verified','two_records_read_verified'):
        if receipt.get(key) is not True:
            raise ValueError('Actual baseline/source/SDK/two-record read proof is incomplete')
    for key in ('accepted_commit_sha','baseline_source_sha256','business_contract_sha256','booking_record_sha256'):
        if receipt.get(key) != fixture.get(key):
            raise ValueError('Current accepted source/SDK/two-record hashes differ')
    if set(fixture['business_contract_sha256']) != set(SDK_PATHS) or len(fixture['booking_record_sha256']) != 2:
        raise ValueError('Exact reviewed SDK paths and two record fingerprints are required')
    for value in [fixture['baseline_source_sha256'],*fixture['business_contract_sha256'].values(),*fixture['booking_record_sha256'].values()]:
        if not isinstance(value,str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('Exact baseline fingerprints are required')
    for value in fixture['booking_record_sha256']:
        UUID(value)
    if len(fixture['accepted_commit_sha']) != 40 or any(c not in '0123456789abcdef' for c in fixture['accepted_commit_sha']):
        raise ValueError('Exact accepted commit is required')


def require_quiescent(state):
    if state.get('generation_lease_present') is not False or any(type(state.get(key)) is not int or state[key] != 0 for key in ('active_generation','active_restoration','active_operation','active_activity','prior_anchor_pause')):
        raise ValueError('Active/unknown work or prior anchor pause prohibits the transition')


def reserve_marker(directory, project_id):
    directory.mkdir(parents=True,exist_ok=True)
    os.chmod(directory,0o700)
    marker=directory/(project_id+'.json')
    fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as file:
        json.dump({'pause_attempted':True,'at':datetime.now(UTC).isoformat()},file)
        file.flush();os.fsync(file.fileno())
    for parent in (directory,directory.parent):
        fd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    return marker


def load_payload(args, stdin=None):
    if args.stdin:
        if args.fixture or args.ack or args.pre_receipt:
            raise ValueError('Use either protected paths or one private stdin payload')
        payload=json.load(stdin or sys.stdin)
        return payload['fixture'],payload['ack'],payload['pre_receipt']
    if not all((args.fixture,args.ack,args.pre_receipt)):
        raise ValueError('Protected fixture/ACK/receipt paths or private stdin are required')
    return tuple(json.loads(Path(path).read_text()) for path in (args.fixture,args.ack,args.pre_receipt))


async def pause(args):
    fixture,ack,receipt=load_payload(args)
    validate_inputs(fixture,ack,receipt,args.revision)
    if args.check_only:
        return {'status':'INPUTS_VALIDATED_NO_DB_NETWORK_OR_LIFECYCLE'}
    from fastapi import Response
    from sqlalchemy import func,select
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from yleum_api.core.config import get_settings
    from yleum_api.core.db import get_engine,dispose_engine
    from yleum_api.models.project import Project
    from yleum_api.models.user import User
    from yleum_api.models.snapshot import Snapshot
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.project_cell import ProjectCellWorkspace,ProjectCellOperation,ProjectCellActivityLease
    from yleum_api.models.restoration import Restoration,ACTIVE_RESTORATION_STATES
    from yleum_api.services.generation_runs import ACTIVE_GENERATION_STATUSES
    from yleum_api.services.orchestrator_client import HttpProjectCellOrchestratorClient
    from yleum_api.services.project_cell_capacity import hibernate_one_idle_workspace
    from yleum_api.services.project_cells import ACTIVE_OPERATION_STATUSES
    from yleum_api.services.generation.agent_finalization import _is_edit_source_path
    from yleum_api.services import repo
    from yleum_api.routers.project_versions import list_project_versions
    from yleum_api.services.project_cell_runtime import _get_cell_resources
    if get_settings().omnia_release_sha != args.revision:
        raise ValueError('Actual API process revision differs from final ACK')
    factory=async_sessionmaker(get_engine(),expire_on_commit=False)
    owner_id,project_id,workspace_id,run_id=(UUID(fixture[key]) for key in ('owner_id','project_id','workspace_id','terminal_run_id'))
    async def verify_baseline(session):
        owner=await session.get(User,owner_id)
        project=await session.get(Project,project_id)
        workspace=await session.get(ProjectCellWorkspace,workspace_id)
        run=await session.get(GenerationRun,run_id)
        if not (owner and owner.role=='user' and project and project.owner_id==owner_id
                and str(project.current_snapshot_id)==fixture['accepted_snapshot_id']
                and workspace and workspace.owner_id==owner_id and workspace.project_id==project_id
                and workspace.provider=='docker_owner_canary' and workspace.deleted_at is None
                and run and run.user_id==owner_id and run.project_id==project_id and run.status=='completed'):
            raise ValueError('Authoritative OWN B identity/current accepted baseline differs')
        latest=await session.scalar(select(GenerationRun.id).where(GenerationRun.project_id==project_id).order_by(GenerationRun.created_at.desc()).limit(1))
        if latest!=run_id:
            raise ValueError('A newer project run exists')
        versions=await list_project_versions(project_id,Response(),session,owner,limit=100)
        if not any(str(row.id)==fixture['accepted_version_id'] and row.is_current and row.status=='ready' and row.prompt_text.strip() for row in versions.versions):
            raise ValueError('Exact current accepted ready user version differs')
        snapshot=await session.get(Snapshot,project.current_snapshot_id)
        if snapshot is None or snapshot.project_id!=project_id or snapshot.commit_sha!=fixture['accepted_commit_sha']:
            raise ValueError('Exact accepted commit differs')
        files=await asyncio.to_thread(repo.read_files,project_id,snapshot.commit_sha)
        hashes={path:digest(content) for path,content in files.items() if _is_edit_source_path(path)}
        actual=digest(json.dumps(hashes,sort_keys=True,separators=(',',':')))
        sdk={path:digest(files[path]) for path in SDK_PATHS}
        if actual!=fixture['baseline_source_sha256'] or sdk!=fixture['business_contract_sha256']:
            raise ValueError('Actual immutable accepted source/SDK differs')
        return workspace
    try:
        async with factory() as session:
            workspace=await verify_baseline(session)
            epoch=workspace.fencing_epoch
            async def count(model,*conditions):
                return int(await session.scalar(select(func.count()).select_from(model).where(*conditions)))
            state={'generation_lease_present':workspace.generation_run_id is not None,
                'active_generation':await count(GenerationRun,GenerationRun.project_id==project_id,GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES)),
                'active_restoration':await count(Restoration,Restoration.workspace_id==workspace_id,Restoration.state.in_(ACTIVE_RESTORATION_STATES)),
                'active_operation':await count(ProjectCellOperation,ProjectCellOperation.workspace_id==workspace_id,ProjectCellOperation.status.in_((*ACTIVE_OPERATION_STATUSES,'indeterminate'))),
                'active_activity':await count(ProjectCellActivityLease,ProjectCellActivityLease.workspace_id==workspace_id,ProjectCellActivityLease.state=='active'),
                'prior_anchor_pause':await count(ProjectCellOperation,ProjectCellOperation.workspace_id==workspace_id,ProjectCellOperation.idempotency_key.startswith(f'capacity:{run_id}:pause:{workspace_id}'))}
            require_quiescent(state)
            resources=await _get_cell_resources(workspace_id)
            if workspace.state!='ready' or resources.state!='resources_ready' or resources.draft_state!='running':
                raise ValueError('Expected exact quiescent running OWN B is unavailable')
        validate_inputs(fixture,ack,receipt,args.revision)
        reserve_marker(Path(args.marker_dir),str(project_id))
        paused=await asyncio.wait_for(hibernate_one_idle_workspace(factory,requesting_run_id=run_id,client=HttpProjectCellOrchestratorClient(),expected_workspace_id=workspace_id),timeout=300)
        if not paused:
            raise ValueError('Normal targeted idle SDK did not certify completed pause')
        async with factory() as session:
            workspace=await verify_baseline(session)
            resources=await _get_cell_resources(workspace_id)
            if workspace.state!='stopped' or workspace.generation_run_id is not None or workspace.fencing_epoch<=epoch or resources.state!='resources_paused':
                raise ValueError('Exact higher-fence physical paused receipt is unavailable')
        return {'status':'OWN_B_NORMAL_SDK_PAUSED_SOURCE_VERSION_PRESERVED_POST_WAKE_DATA_PROOF_PENDING','model_calls':0,'lease_repairs':0,'wake_owner':'root_normal_owned_Studio_only','attempt_marker_retained':True}
    finally:
        await dispose_engine()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--fixture')
    parser.add_argument('--ack')
    parser.add_argument('--pre-receipt')
    parser.add_argument('--stdin',action='store_true',help='Private metadata/hash JSON payload; never publish it')
    parser.add_argument('--revision',required=True)
    parser.add_argument('--marker-dir',default='/tmp/qa-owned-B-trust-refresh-attempts')
    parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args()
    try:
        result=asyncio.run(pause(args))
    except Exception as error:
        print(json.dumps({'status':'STOPPED_ROOT_RECONCILIATION_REQUIRED','error_type':type(error).__name__,'auto_retry':False}));raise SystemExit(2)
    print(json.dumps(result))
