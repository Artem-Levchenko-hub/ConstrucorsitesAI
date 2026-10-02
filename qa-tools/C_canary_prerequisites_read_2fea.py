"""New distinct-task C prerequisites READ; reuses frozen native probe unchanged."""
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

REV = '2fea1db74ba58758714063ec57be76e975fb0716'
HELPER_SHA = 'cd47b12332ba984a80d77d91e29af1223ce284c06aad0d0cc461233ab5c796b9'
ROOT = Path('/tmp/qa-C-canary-prereq-2fea')
CODE = r'''
import asyncio,contextlib,hashlib,importlib.util,io,json,logging,os,sys
from pathlib import Path
from uuid import UUID
from datetime import datetime,timezone
p=Path('/tmp/Cp2.py')
assert hashlib.sha256(p.read_bytes()).hexdigest()=='cd47b12332ba984a80d77d91e29af1223ce284c06aad0d0cc461233ab5c796b9'
spec=importlib.util.spec_from_file_location('frozen_native',p)
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
data=json.load(sys.stdin);h.validate(data)
# Preserve prior historical receipts; new read purpose gets a new exclusive receipt.
fd=h.reserve_receipt('/tmp/qa-C-canary-prereq-2fea-native-private.json')
async def read():
 from yleum_api.core.config import get_settings
 from yleum_api.services import orchestrator_hosts as hosts
 from yleum_api.services.project_cell_runtime import _get_cell_resources
 assert get_settings().omnia_release_sha==data['revision']
 native=await asyncio.wait_for(h.protected_probe(data),timeout=60)
 h.write_receipt(fd,native)
 assert native['typed_quiescence_confirmed'] is True
 assert all(x['terminal_state']=='completed' and x['ok'] is True and x['exit_code']==0 and not x['timed_out'] for x in native['native_command_journals'])
 ids=h.RAW_IDS
 w=UUID(ids['workspace_id']);hosts.remember_workspace_host(w,UUID(ids['project_id']),'commerce')
 resources=await asyncio.wait_for(_get_cell_resources(w),timeout=12)
 assert resources.workspace_id==w
 safe={'state':resources.state,'draft_state':resources.draft_state,'has_workspace':resources.has_workspace,'has_agent_home':resources.has_agent_home,'has_postgres':resources.has_postgres,'has_redis':resources.has_redis,'has_draft_runtime':resources.has_draft_runtime,'preview_url_present':bool(resources.preview_url),'GETs':1,'POSTs':0}
 assert all(safe[k] is True for k in ('has_workspace','has_agent_home','has_postgres','has_redis','has_draft_runtime','preview_url_present'))
 return {'status':'ACTUAL_FRESH_C_CANARY_PREREQUISITES_READ','captured_at_utc':datetime.now(timezone.utc).isoformat(),'revision':data['revision'],'native':native,'resources':safe,'new_models':0,'new_native_commands':0,'lifecycle_actions':0}
logging.disable(logging.CRITICAL)
with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
 result=asyncio.run(read())
print(json.dumps(result))
'''

def call(args, **kwargs):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, **kwargs).stdout

def save(name, value):
    with os.fdopen(os.open(ROOT/name, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600), 'w') as f:
        json.dump(value, f)

def run():
    delivery=json.loads(Path('/tmp/qa-integrated-release-'+REV+'/state.json').read_text())
    assert delivery['phase']=='complete' and delivery['checks']['canonicalExitCode']==delivery['checks']['backupExitCode']==0
    import importlib.util
    helper=Path('/tmp/Cp2.py');assert hashlib.sha256(helper.read_bytes()).hexdigest()==HELPER_SHA
    spec=importlib.util.spec_from_file_location('frozen_native',helper);h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
    before=json.loads(call(['docker','inspect','yleum-prod-api']))[0]
    assert before['State']['Running']
    cid=before['Id'];assert len(cid)==64
    data=json.loads(Path('/tmp/C-current-native-resources-2fea/root-read-ack.json').read_text())
    data.update(revision=REV,api_container_id=cid)
    data['read_ack'].update(scope_sha256=h.scope(data),issued_at_utc=datetime.now(UTC).isoformat(),production_delivered=True)
    h.validate(data);save('fresh-read-ack.json',data)
    raw=call(['docker','exec','-i',cid,'python','-E','-c',CODE],input=json.dumps(data).encode())
    result=json.loads(raw)
    assert json.loads(call(['docker','inspect','yleum-prod-api']))[0]['Id']==cid
    assert result['revision']==REV and result['native']['typed_quiescence_confirmed']
    result['api_container_binding_verified']=True
    result['actual_api_container_id']=cid
    call(['docker','cp',cid+':/tmp/qa-C-canary-prereq-2fea-native-private.json',str(ROOT/'native-private.json')])
    return result

if __name__=='__main__':
    os.umask(0o077);ROOT.mkdir(mode=0o700)
    try: result=run()
    except Exception as e: result={'status':'FRESH_C_CANARY_PREREQ_READ_BLOCKED','error_type':type(e).__name__,'new_models':0,'lifecycle_actions':0}
    save('result.json',result);print(json.dumps(result))
