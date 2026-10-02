"""Frozen exact OWN_B canary terminal proof. Prepare-only default, SELECT/GET only.
Execute only by root inside final API container, stdin protected fixture + run_id.
Output is an OWN QA private receipt: root encrypts with its public certificate.
No model, command submit, runtime ensure, reconciliation, lease change or log dump.
"""
import argparse,asyncio,hashlib,json,re,sys
from datetime import UTC,datetime
from decimal import Decimal
from uuid import UUID
RELEASE='8fe8cff8bcd004dd61845d9b7d019114b3ae7707'
RUN_ID_SHA256='38ab346ab195826c007ea39dfc6ccd6058318faf0636a8512e48f8cd626fab02'
PHASES={'prepare','edit','fast_check','final_build','runtime_probe','snapshot','promote','complete','full_build','build'}
TERMINAL={'completed','failed','cancelled'}
FAILURE_PHASE='prepare'
PHASE_LABELS={'prepare','input','release','transaction','scope','latest_run','coordinator','native_boundary_events','unresolved_counts','workspace','snapshots','versions','source','activities','activity_count','operations','proof_dimensions','candidates','usage','settlements','wallet_charges','journal','complete'}
def phase(label):
 global FAILURE_PHASE
 if label not in PHASE_LABELS:raise ValueError('Invalid diagnostic phase')
 FAILURE_PHASE=label

def sql_error_metadata(exc):
 # Never serialize str(exc), query/statement/params, raw messages or diagnostics.
 result={'error_type':type(exc).__name__,'failure_phase':FAILURE_PHASE}
 current=exc;seen=set()
 for _ in range(5):
  if current is None or id(current) in seen:break
  seen.add(id(current))
  classname=type(current).__name__
  if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}',classname):result['driver_error_class']=classname
  for field in ('sqlstate','pgcode'):
   value=getattr(current,field,None)
   if isinstance(value,str) and re.fullmatch(r'[A-Z0-9]{5}',value):result['sqlstate']=value
  for field in ('table_name','column_name','schema_name'):
   value=getattr(current,field,None)
   if isinstance(value,str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}',value):result[field]=value
  current=getattr(current,'orig',None) or getattr(current,'__cause__',None)
 return result

def sha(v):return hashlib.sha256(str(v).encode()).hexdigest()
def known(v,allowed):return v if v in allowed else None
def finite(v):
 d=Decimal(str(v))
 if not d.is_finite() or d<0:raise ValueError('Finite nonnegative accounting aggregate required')
 return str(d)
def error(v):
 return {'present':bool(v),'sha256':sha(v) if v else None,'class':next((x for x in ('OrchestratorUnavailable','TimeoutError','RuntimeError','ValueError','ApiError','PermissionError') if re.search(r'\b'+x+r'\b',str(v or ''))),None)}
def validate(data):
 f=data['fixture'];run=UUID(data['run_id'])
 if f.get('fixture_label')!='B' or f.get('owner_role')!='user' or data.get('revision')!=RELEASE or sha(run)!=RUN_ID_SHA256:raise ValueError('Only exact authorized final B edit run')
 for key in ('owner_id','project_id','workspace_id','accepted_snapshot_id','accepted_version_id'):UUID(f[key])
 return f,run

def coordinator(raw):
 state=raw.get('max_finalization',{}) if isinstance(raw,dict) else {};state=state if isinstance(state,dict) else {};cp=state.get('checkpoint',{});cp=cp if isinstance(cp,dict) else {}
 result={'present':bool(state),'phase':known(state.get('current_phase'),PHASES),'checkpoint_phase':known(cp.get('phase'),PHASES),'outcome':known(state.get('outcome'),{'complete','completed','failed','cancelled','needs_edit','activating'}),'terminal_error':error(state.get('terminal_reason'))}
 for key,allowed in [('phase_ms',PHASES),('counters',{'bootstrap','fast_check','full_build','runtime_probe','proof_hit'})]:
  values=state.get(key,{});result[key]={k:v for k,v in values.items() if k in allowed and type(v)is int and v>=0} if isinstance(values,dict) else {}
 return result

async def probe(data):
 phase('input');f,run_id=validate(data)
 from fastapi import Response
 from sqlalchemy import select,func,text,case,and_
 from sqlalchemy.ext.asyncio import async_sessionmaker
 from yleum_api.core.config import get_settings
 from yleum_api.core.db import get_engine,dispose_engine
 from yleum_api.models.user import User
 from yleum_api.models.project import Project
 from yleum_api.models.generation_run import GenerationRun
 from yleum_api.models.generation_event import GenerationEvent
 from yleum_api.models.snapshot import Snapshot
 from yleum_api.models.project_cell import ProjectCellWorkspace,ProjectCellOperation,ProjectCellActivityLease,ProjectCellProof,ProjectCellProofResult,ProjectCellCandidate
 from yleum_api.models.usage import Usage
 from yleum_api.models.usage_settlement import UsageSettlement
 from yleum_api.models.wallet_charge import WalletCharge
 from yleum_api.routers.project_versions import list_project_versions
 from yleum_api.services import orchestrator_client,repo
 from yleum_api.services.generation.agent_finalization import _is_edit_source_path
 phase('release')
 if get_settings().omnia_release_sha!=RELEASE:raise ValueError('Actual API revision differs')
 result={'captured_utc':datetime.now(UTC).isoformat(),'revision':RELEASE,'readonly':True,'exact_run_sha256':RUN_ID_SHA256,'model_calls':0,'new_commands':0,'lifecycle_actions':0,'log_dump':False}
 factory=async_sessionmaker(get_engine(),expire_on_commit=False);journal_ids=[]
 try:
  async with factory() as s:
   phase('transaction')
   await s.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'));await s.execute(text("SET LOCAL statement_timeout='15000ms'"))
   phase('scope')
   u=await s.get(User,UUID(f['owner_id']));p=await s.get(Project,UUID(f['project_id']));w=await s.get(ProjectCellWorkspace,UUID(f['workspace_id']));r=await s.get(GenerationRun,run_id)
   if not(u and u.role=='user' and p and p.owner_id==u.id and w and w.owner_id==u.id and w.project_id==p.id and w.provider=='docker_owner_canary' and w.deleted_at is None and r and r.user_id==u.id and r.project_id==p.id):raise ValueError('Exact authoritative ordinary owned scope differs')
   result['run']={'status':known(r.status,TERMINAL|{'pending','queued_for_capacity','running','cancel_requested'}),'response_mode':known(r.response_mode,{'edit','build','clarify'}),'execution_backend':known(r.execution_backend,{'api','worker'}),'created_at':r.created_at.isoformat(),'started_at':r.started_at.isoformat() if r.started_at else None,'execution_started_at':r.execution_started_at.isoformat() if r.execution_started_at else None,'finished_at':r.finished_at.isoformat() if r.finished_at else None,'error':error(r.error)}
   if r.status not in TERMINAL:return {**result,'status':'NOT_TERMINAL_NO_JOURNAL_OR_ACCOUNTING_READ'}
   phase('latest_run')
   latest=await s.scalar(select(GenerationRun.id).where(GenerationRun.project_id==p.id).order_by(GenerationRun.created_at.desc()).limit(1));result['exact_scope']={'ordinary_owner':True,'latest_run_same':latest==run_id,'workspace_bound':True}
   phase('coordinator')
   result['coordinator']=coordinator(r.agent_state)
   phase('native_boundary_events')
   native_kind=GenerationEvent.payload['kind'].astext
   boundaries=(await s.execute(select(native_kind,func.count()).where(GenerationEvent.generation_run_id==run_id,GenerationEvent.project_id==p.id,GenerationEvent.event_type=='agent.step',native_kind.in_(('done','stalled'))).group_by(native_kind))).all()
   result['native_boundary_event_counts']={kind:count for kind,count in boundaries}
   phase('unresolved_counts')
   result['unresolved_exact_run']={'active_activity':int(await s.scalar(select(func.count()).select_from(ProjectCellActivityLease).where(ProjectCellActivityLease.workspace_id==w.id,ProjectCellActivityLease.generation_run_id==run_id,ProjectCellActivityLease.state=='active'))),'active_or_indeterminate_operation':int(await s.scalar(select(func.count()).select_from(ProjectCellOperation).where(ProjectCellOperation.workspace_id==w.id,ProjectCellOperation.generation_run_id==run_id,ProjectCellOperation.status.in_(('pending','waiting_capacity','running','indeterminate')))))}
   phase('workspace')
   result['workspace']={'state':known(w.state,{'provisioning','ready','stopped','failed','deleting','deleted'}),'fencing_epoch':w.fencing_epoch,'generation_lease_present':w.generation_run_id is not None,'error':error(w.last_error)}
   phase('snapshots')
   result['snapshots']={'total':int(await s.scalar(select(func.count()).select_from(Snapshot).where(Snapshot.project_id==p.id))),'accepted_baseline_still_current':str(p.current_snapshot_id)==f['accepted_snapshot_id']}
   phase('versions')
   versions=await list_project_versions(p.id,Response(),s,u,limit=100);current=[v for v in versions.versions if v.is_current]
   result['current_version']={'count':len(current),'status':known(current[0].status,{'queued','running','ready','failed','cancelled','unchanged'}) if len(current)==1 else None,'same_as_baseline':len(current)==1 and str(current[0].id)==f['accepted_version_id'],'has_nonempty_prompt':len(current)==1 and bool(current[0].prompt_text.strip()),'matches_edit_run':len(current)==1 and current[0].generation_run_id==run_id}
   phase('source')
   snap=await s.get(Snapshot,p.current_snapshot_id) if p.current_snapshot_id else None
   if snap and snap.project_id==p.id:
    files=await asyncio.to_thread(repo.read_files,p.id,snap.commit_sha);hashes={path:sha(content) for path,content in files.items() if _is_edit_source_path(path)}
    result['current_source']={'commit_sha':snap.commit_sha,'file_count':len(files),'source_fingerprint':sha(json.dumps(hashes,sort_keys=True,separators=(',',':'))),'SDK_sha256':{path:sha(files[path]) for path in ('src/lib/omnia/integration-client.ts','src/lib/omnia/max-config.ts','src/components/MaxAppProvider.tsx') if path in files}}
   if result.get('current_source'):
    result['current_source']['SDK_matches_pre_edit_baseline']=result['current_source']['SDK_sha256']==f['business_contract_sha256']
   phase('activities')
   acts=(await s.scalars(select(ProjectCellActivityLease).where(ProjectCellActivityLease.workspace_id==w.id,ProjectCellActivityLease.generation_run_id==run_id).order_by(ProjectCellActivityLease.started_at.desc()).limit(15))).all()
   result['activities']=[{'operation_sha256':sha(a.operation_id),'kind':known(a.kind,{'command','tool','finalization','snapshot','promotion'}),'state':known(a.state,{'active','completed','failed','timed_out','cancelled'}),'phase':known(a.phase,PHASES),'fencing_epoch':a.fencing_epoch,'started_at':a.started_at.isoformat(),'heartbeat_at':a.heartbeat_at.isoformat(),'deadline_at':a.deadline_at.isoformat(),'finished_at':a.finished_at.isoformat() if a.finished_at else None,'log_bytes':a.log_bytes,'error':error(a.redacted_diagnostic)} for a in acts]
   phase('activity_count')
   result['activity_total']=int(await s.scalar(select(func.count()).select_from(ProjectCellActivityLease).where(ProjectCellActivityLease.workspace_id==w.id,ProjectCellActivityLease.generation_run_id==run_id)))
   # Existing command/finalization leases are authoritative agent journal IDs.
   journal_ids=[a.operation_id for a in acts if a.kind in {'command','finalization','tool'}][:3]
   phase('operations')
   ops=(await s.scalars(select(ProjectCellOperation).where(ProjectCellOperation.workspace_id==w.id,ProjectCellOperation.generation_run_id==run_id).order_by(ProjectCellOperation.created_at.desc()).limit(15))).all()
   result['operations']=[{'kind':known(o.kind,{'ensure','wake','pause','stop','destroy','status','restore','reconcile','release'}),'status':known(o.status,{'pending','waiting_capacity','running','completed','failed','cancelled','indeterminate'}),'attempt_count':o.attempt_count,'fencing_epoch':o.fencing_epoch,'finished_at':o.finished_at.isoformat() if o.finished_at else None,'error':error(o.error)} for o in ops]
   phase('proof_dimensions')
   groups=(await s.execute(select(ProjectCellProofResult.dimension,ProjectCellProofResult.outcome,func.count()).join(ProjectCellProof,ProjectCellProof.id==ProjectCellProofResult.proof_id).where(ProjectCellProof.workspace_id==w.id,ProjectCellProof.generation_run_id==run_id,ProjectCellProofResult.workspace_id==w.id).group_by(ProjectCellProofResult.dimension,ProjectCellProofResult.outcome))).all()
   result['proof_dimensions']=[{'dimension':known(d,{'bootstrap','fast_check','full_build','runtime','release'}),'outcome':known(o,{'green','red'}),'count':n} for d,o,n in groups]
   phase('candidates')
   cand=(await s.execute(select(ProjectCellCandidate.status,ProjectCellCandidate.cancelled,func.count()).where(ProjectCellCandidate.workspace_id==w.id,ProjectCellCandidate.generation_run_id==run_id).group_by(ProjectCellCandidate.status,ProjectCellCandidate.cancelled))).all()
   result['candidate_counts']=[{'status':known(st,{'prepared','accepted','rejected','cancelled'}),'cancelled':cancel,'count':n} for st,cancel,n in cand]
   usage_scope=(Usage.user_id==u.id,Usage.project_id==p.id,Usage.run_id==run_id)
   phase('usage')
   row=(await s.execute(select(func.count(Usage.id),func.coalesce(func.sum(Usage.cost_rub),0),func.coalesce(func.sum(Usage.provider_cost_usd),0),func.count(Usage.provider_cost_usd),func.count().filter(and_(Usage.provider_request_id.is_not(None),Usage.provider_request_id!='')),func.count(func.distinct(Usage.provider_request_id)),func.coalesce(func.sum(Usage.tokens_in),0),func.coalesce(func.sum(Usage.tokens_out),0)).where(*usage_scope))).one()
   result['usage']={'rows':row[0],'cost_rub':finite(row[1]),'provider_cost_usd':finite(row[2]),'provider_priced_rows':row[3],'provider_receipt_rows':row[4],'distinct_provider_request_ids':row[5],'tokens_in':row[6],'tokens_out':row[7]}
   phase('settlements')
   settled=(await s.execute(select(UsageSettlement.status,func.count()).join(Usage,Usage.id==UsageSettlement.usage_id).where(*usage_scope,UsageSettlement.user_id==u.id).group_by(UsageSettlement.status))).all()
   result['settlements']={'status_counts':{known(st,{'settled','free','unpaid'}) or 'unknown':n for st,n in settled},'missing_for_exact_usage':int(await s.scalar(select(func.count()).select_from(Usage).outerjoin(UsageSettlement,and_(UsageSettlement.usage_id==Usage.id,UsageSettlement.user_id==u.id)).where(*usage_scope,UsageSettlement.id.is_(None))))}
   phase('wallet_charges')
   charges=(await s.execute(select(func.count(WalletCharge.id),func.coalesce(func.sum(WalletCharge.amount_rub),0)).join(UsageSettlement,UsageSettlement.wallet_charge_id==WalletCharge.id).join(Usage,Usage.id==UsageSettlement.usage_id).where(*usage_scope,UsageSettlement.user_id==u.id,WalletCharge.user_id==u.id,WalletCharge.entry_type=='usage'))).one()
   # Actual wallet amount can be signed; preserve aggregate, never invent debit sign.
   charge_amount=Decimal(str(charges[1]));
   if not charge_amount.is_finite():raise ValueError('Nonfinite actual wallet charge aggregate')
   result['wallet_exact_usage_charges']={'rows':charges[0],'signed_amount_rub':str(charge_amount),'scope':'only wallet charges FK-linked to this exact run UsageSettlement; no window approximation'}
   workspace_id=w.id
  # DB read snapshot closed before bounded existing SDK GETs. No commands submitted.
  result['controller_journals']=[]
  phase('journal')
  for operation_id in journal_ids:
   entry={'operation_sha256':sha(operation_id)}
   try:
    j=await asyncio.wait_for(orchestrator_client.project_cell_agent_operation_status(workspace_id,operation_id),timeout=30)
    if j.operation_id!=operation_id:raise ValueError('Exact journal operation identity differs')
    terminal=j.terminal_response
    if terminal and terminal.operation_id!=operation_id:raise ValueError('Exact terminal identity differs')
    entry.update({'state':known(j.state,{'running','completed','failed','timed_out','cancelled'}),'phase':known(j.phase,PHASES),'log_bytes':j.log_bytes,'heartbeat_at':j.heartbeat_at.isoformat(),'terminal_present':terminal is not None,'terminal':{'ok':terminal.ok,'exit_code':terminal.exit_code,'timed_out':terminal.timed_out,'before_identity_present':terminal.before_identity is not None,'after_identity_present':terminal.after_identity is not None,'environment_mutated':terminal.environment_mutated,'error':error(terminal.detail)} if terminal else None})
   except Exception as e:entry['read_error_type']=type(e).__name__
   result['controller_journals'].append(entry)
  phase('complete')
  result['status']='ACTUAL_OWN_B_TERMINAL_NATIVE_AND_ACCOUNTING_METADATA_READ';result['finished_utc']=datetime.now(UTC).isoformat();result['acceptance_note']='Observed facts only; caller must evaluate exact edit result, billing completeness, current source, UI preservation and cleanup separately.'
  return result
 finally:await dispose_engine()

def main():
 p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');a=p.parse_args()
 if not a.execute:print(json.dumps({'status':'PREPARED_ONLY_NO_DB_NETWORK'}));return
 try:result=asyncio.run(asyncio.wait_for(probe(json.load(sys.stdin)),timeout=180))
 except Exception as e:result={'status':'READONLY_EXACT_RUN_PROOF_BLOCKED',**sql_error_metadata(e),'partial_facts_accepted':False}
 print(json.dumps(result));sys.exit(0 if result.get('status')=='ACTUAL_OWN_B_TERMINAL_NATIVE_AND_ACCOUNTING_METADATA_READ' else 2)
if __name__=='__main__':main()
