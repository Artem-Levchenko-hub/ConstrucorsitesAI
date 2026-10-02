"""Prepare-only Core API container probe. Private combined payload stdin; GET/SELECT only.
Never reconciliation, lifecycle dispatch, environment read/dump or business rows.
"""
import argparse,asyncio,json,hashlib,sys,re
from datetime import UTC,datetime
from uuid import UUID
EXPECTED='8fe8cff8bcd004dd61845d9b7d019114b3ae7707'

def validate_payload(data):
 f=data['fixture'];a=data['ack']
 if f.get('fixture_label')!='B' or f.get('owner_role')!='user' or a.get('sha')!=EXPECTED:raise ValueError('Exact final owned B fixture required')
 for k in ('owner_id','project_id','workspace_id','terminal_run_id','accepted_snapshot_id','accepted_version_id'):UUID(f[k])
 if any(a.get(k)!=f[k] for k in ('owner_id','project_id','workspace_id')):raise ValueError('Exact owned target differs')
 return f

def error_summary(value):
 if not value:return {'error_present':False}
 classes=('OrchestratorUnavailable','TimeoutError','OrchestratorTimeout','ProjectCellUnavailable','RuntimeError','ValueError','ApiError')
 return {'error_present':True,'error_sha256':hashlib.sha256(str(value).encode()).hexdigest(),'known_error_class':next((x for x in classes if re.search(r'\b'+x+r'\b',str(value))),None)}

async def probe(data):
 f=validate_payload(data)
 from sqlalchemy import select,func,text
 from sqlalchemy.ext.asyncio import async_sessionmaker
 from yleum_api.core.config import get_settings
 from yleum_api.core.db import get_engine,dispose_engine
 from yleum_api.models.project import Project
 from yleum_api.models.user import User
 from yleum_api.models.generation_run import GenerationRun
 from yleum_api.models.project_cell import ProjectCellWorkspace,ProjectCellOperation,ProjectCellActivityLease
 from yleum_api.models.restoration import Restoration,ACTIVE_RESTORATION_STATES
 from yleum_api.services.generation_runs import ACTIVE_GENERATION_STATUSES
 from yleum_api.services.project_cells import ACTIVE_OPERATION_STATUSES
 from yleum_api.services.project_cell_runtime import _get_cell_resources
 from yleum_api.services import orchestrator_client
 if get_settings().omnia_release_sha!=EXPECTED:raise ValueError('Actual API process revision differs')
 result={'captured_utc':datetime.now(UTC).isoformat(),'release':EXPECTED,'database_read_only':True,'model_calls':0,'lifecycle_actions':0}
 factory=async_sessionmaker(get_engine(),expire_on_commit=False)
 try:
  async with factory() as s:
   await s.execute(text('SET TRANSACTION READ ONLY'))
   owner=await s.get(User,UUID(f['owner_id']));p=await s.get(Project,UUID(f['project_id']));w=await s.get(ProjectCellWorkspace,UUID(f['workspace_id']));r=await s.get(GenerationRun,UUID(f['terminal_run_id']))
   if not(owner and owner.role=='user' and p and p.owner_id==owner.id and w and w.owner_id==owner.id and w.project_id==p.id and w.deleted_at is None and r and r.user_id==owner.id and r.project_id==p.id):raise ValueError('Authoritative owned scope differs')
   async def count(m,*conds):return int(await s.scalar(select(func.count()).select_from(m).where(*conds)))
   result['workspace']={'owned_exact_fixture':True,'provider':w.provider,'state':w.state,'fencing_epoch':w.fencing_epoch,'generation_lease_present':w.generation_run_id is not None,**error_summary(w.last_error)}
   result['baseline']={'accepted_snapshot_same':str(p.current_snapshot_id)==f['accepted_snapshot_id'],'terminal_run_same':str(r.id)==f['terminal_run_id'],'terminal_run_status':r.status,'latest_run_same':await s.scalar(select(GenerationRun.id).where(GenerationRun.project_id==p.id).order_by(GenerationRun.created_at.desc()).limit(1))==r.id}
   result['active_counts']={'generation':await count(GenerationRun,GenerationRun.project_id==p.id,GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES)),'restoration':await count(Restoration,Restoration.workspace_id==w.id,Restoration.state.in_(ACTIVE_RESTORATION_STATES)),'operation':await count(ProjectCellOperation,ProjectCellOperation.workspace_id==w.id,ProjectCellOperation.status.in_((*ACTIVE_OPERATION_STATUSES,'indeterminate'))),'activity':await count(ProjectCellActivityLease,ProjectCellActivityLease.workspace_id==w.id,ProjectCellActivityLease.state=='active')}
   ops=(await s.scalars(select(ProjectCellOperation).where(ProjectCellOperation.workspace_id==w.id,ProjectCellOperation.kind.in_(('pause','wake'))).order_by(ProjectCellOperation.created_at.desc()).limit(5))).all()
   result['latest_normal_operations']=[{'kind':o.kind,'status':o.status,'fencing_epoch':o.fencing_epoch,'created_at':o.created_at.isoformat(),'started_at':o.started_at.isoformat() if o.started_at else None,'finished_at':o.finished_at.isoformat() if o.finished_at else None,'attempt_count':o.attempt_count,**error_summary(o.error)} for o in ops]
   try:
    resources=await asyncio.wait_for(_get_cell_resources(w.id),timeout=40)
    result['physical_resources']={k:getattr(resources,k) for k in ('state','fencing_epoch','has_workspace','has_agent_home','has_postgres','has_redis','has_draft_runtime','draft_state')}
    result['physical_resources']['workspace_identity_matches']=resources.workspace_id==w.id
   except Exception as e:result['physical_resources']={'read_error_type':type(e).__name__}
   # Supported GET journal is specifically AGENT operations, not normal wake/pause.
   # Do not guess an endpoint or query a wrong operation ID as evidence.
   result['normal_operation_journal']={'supported_GET_endpoint':False,'scope_note':'Normal lifecycle receipt above is authoritative API operation metadata; controller normal journal needs separate root read-only provider-state probe. Agent GET must not be misused for lifecycle UUID.'}
   if data.get('known_agent_operation_id'):
    oid=UUID(data['known_agent_operation_id'])
    known=await s.get(ProjectCellOperation,oid)
    if known is None or known.workspace_id!=w.id or known.kind in ('pause','wake','ensure','stop','destroy'):raise ValueError('Known agent operation metadata required')
    try:
     j=await asyncio.wait_for(orchestrator_client.project_cell_agent_operation_status(w.id,oid),timeout=40)
     result['known_agent_journal']={'state':getattr(j,'state',None),'terminal_present':getattr(j,'terminal',None) is not None}
    except Exception as e:result['known_agent_journal']={'read_error_type':type(e).__name__}
   result['status']='ACTUAL_READONLY_DB_RESOURCE_DIAGNOSED'
  return result
 finally:await dispose_engine()

def main():
 p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');a=p.parse_args()
 if not a.execute:print(json.dumps({'status':'PREPARED_ONLY_NO_DB_NETWORK'}));return
 try:result=asyncio.run(asyncio.wait_for(probe(json.load(sys.stdin)),timeout=100))
 except Exception as e:result={'status':'READONLY_DIAGNOSIS_BLOCKED','error_type':type(e).__name__}
 print(json.dumps(result));sys.exit(0 if result.get('status')=='ACTUAL_READONLY_DB_RESOURCE_DIAGNOSED' else 2)
if __name__=='__main__':main()
