"""Guarded wrapper around the repository's existing canonical production release."""
import argparse
import pwd
import json
import hashlib
import os
import re
import subprocess
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path('/opt/omnia')
PARENT = '4f8bbbe6221c7836eaf794f7ee8e37d3f7be0473'
HISTORICAL_COMMERCE_LOCK_SHA = 'c21aeeb61b81c9c571f34e77183fe9cf0b419888'
HISTORICAL_COMMERCE_LOCK_TIME = '2026-09-29T19:23:25Z'

def save(phase):
    state['phase'] = phase
    STATE.write_text(json.dumps(state))
    print(phase, flush=True)

def run(args, *, data=None):
    return subprocess.run(args, input=data, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True).stdout

def git(*args):
    return run(['git', '-C', str(ROOT), *args]).strip()

def remote(code):
    return run(['ssh', '-o', 'BatchMode=yes', 'commerce', 'python3', '-'], data=code)

def effective_code_dirty(paths):
    return [p for p in paths if p.startswith(('apps/', 'infra/', '.github/'))
            and not Path(p).name.startswith(('.env.pre-', '.env.before-', '.env.bak'))
            and not (Path(p).parent.as_posix() in {'apps/llm-gateway/deploy/full', 'apps/orchestrator'}
                     and re.fullmatch(r'[.]env[.](?:app-data-[0-9a-f]{40}[.]before|candidate-preview-[0-9a-f]{7,40})', Path(p).name))]


def require_idle_preflight(status):
    assert status.get('release_sha') is None, 'Existing deployment fence is present; preserved unchanged'
    counts = status.get('active')
    expected = {'generations', 'operations', 'leases', 'restorations'}
    assert isinstance(counts, dict) and set(counts) == expected, 'Old-live active-work status is incomplete'
    assert all(type(value) is int and value == 0 for value in counts.values()), 'Old-live active work is not zero; reconcile normally before release'


def require_release_health(health, expected):
    assert health.get('status') == 'ok' and health.get('release_sha') == expected, 'Actual API release/health mismatch'
    checks = health.get('checks')
    assert isinstance(checks, dict) and checks and all(value == 'ok' for value in checks.values()), 'Actual API health checks are not all green'
    dependencies = health.get('dependencies', {})
    for key in ('generation_worker_release_sha', 'worker_release_sha', 'orchestrator_release_sha'):
        assert dependencies.get(key) == expected, 'Worker/controller runtime revision mismatch'
    prefix = dependencies.get('billing_worker_release_sha')
    assert (isinstance(prefix, str) and prefix in (expected, expected[:12])
            and dependencies.get('billing_worker') == 'ok'), 'Billing runtime revision/health mismatch'

INVENTORY = '''import json,subprocess,pathlib,re,urllib.request
r='/opt/omnia'
def g(*a):return subprocess.check_output(['git','-C',r,*a],text=True).splitlines()
lock=pathlib.Path(r+'/.deploy.lock')
processes=subprocess.check_output(['ps','-eo','args'],text=True).splitlines()
out={'head':g('rev-parse','HEAD')[0],'staged':g('diff','--cached','--name-only'),
 'dirty':g('diff','--name-only')+g('ls-files','--others','--exclude-standard'),
 'activeDeployers':sum(bool(re.search(r'(^|[/\\s])deploy-prod[.]sh(?:\\s|$)|docker compose.*build|rsync.*/opt/omnia',p)) for p in processes),
 'lockPresent':lock.exists()}
out['controllerHealth']=json.load(urllib.request.urlopen('http://127.0.0.1:8003/health',timeout=10))
if lock.exists():
 s=lock.read_text().strip().split()
 out['lockRecognized']=len(s)==3 and len(s[-1])==40
 out['lockRevision']=s[-1] if out['lockRecognized'] else None
 out['lockTimestamp']=s[1] if out['lockRecognized'] else None
print(json.dumps(out))
'''


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--ci-run-id', required=True, type=int)
    parser.add_argument('--execute', action='store_true', help='Run the guarded canonical release')
    args = parser.parse_args(argv)
    if re.fullmatch(r'[0-9a-f]{40}', args.sha) is None or args.ci_run_id <= 0:
        parser.error('Full lowercase 40-hex candidate and positive exact CI run ID required')
    return args


def require_web_health(body, expected):
    assert body.get('status') == 'ok' and body.get('service') == 'web', 'Actual web service health mismatch'
    assert body.get('release_sha') == expected, 'Actual web process revision mismatch'


def require_exact_ci(ci, expected):
    assert ci == {'headSha': expected, 'status': 'completed', 'conclusion': 'success'}, 'Exact candidate CI is not completed green'


def require_post_release_status(status):
    require_idle_preflight(status)
    assert status.get('bootstrap_readonly') is False, 'Post-release status must use actual durable authority'


def require_ingress_absent(status):
    assert set(status) == {'activeState','mapPresent','vhostMarkerPresent'}, 'Ingress evidence incomplete'
    assert all(value is False for value in status.values()), 'Ingress admission barrier remains'


def require_canonical_order(source):
    assert 'gateway_cutover_gate()' in source, 'Candidate canonical cutover gate missing'
    markers = ('  ingress_drain begin', 'generation_deployment_drain begin $SHA',
               'git merge --ff-only $SHA', 'gateway_cutover_gate\n',
               '  ingress_drain end', 'generation_deployment_drain end $SHA')
    assert all(marker in source for marker in markers), 'Canonical fence/cutover sequence is incomplete'
    assert source.index('  ingress_drain begin') < source.index('generation_deployment_drain begin $SHA') < source.index('git merge --ff-only $SHA'), 'Admission fences must precede fast-forward'
    assert source.rindex('gateway_cutover_gate\n') < source.index('  ingress_drain end') < source.index('generation_deployment_drain end $SHA'), 'Cutover must precede both normal reopens'


INGRESS_POST = """import json,pathlib
state=pathlib.Path('/var/lib/yleum/generation-ingress-drain/active.json')
marker=b'# yleum-owned-generation-ingress-drain-v1'
print(json.dumps({'activeState':state.exists(),
 'mapPresent':pathlib.Path('/etc/nginx/conf.d/yleum-generation-drain.conf').exists(),
 'vhostMarkerPresent':marker in pathlib.Path('/etc/nginx/sites-available/yleum.ru').read_bytes()}))
"""


def main(argv=None):
    global SHA, CI_RUN_ID, STATE, state
    args = parse_args(argv)
    SHA, CI_RUN_ID = args.sha, args.ci_run_id
    if not args.execute:
        print(json.dumps({'phase':'prepared-only','candidate':SHA,'ciRunId':CI_RUN_ID,'parent':PARENT,'withWeb':True,'withGateway':True}))
        return 0
    # Canonical SSH aliases, Git credentials and backup authority belong to this
    # existing normal Core account; this wrapper never copies keys or modifies SSH.
    assert pwd.getpwuid(os.geteuid()).pw_name == 'zeuszcz', 'Execute from the existing zeuszcz Core account'
    os.umask(0o077)
    receipt = Path('/tmp/qa-integrated-release-' + SHA)
    receipt.mkdir(mode=0o700)  # Exclusive: preserve failed receipts and prevent blind reruns.
    STATE = receipt / 'state.json'
    state = {'candidate':SHA,'parent':PARENT,'ciRunId':CI_RUN_ID,'withWeb':True,'withGateway':True,
             'started':datetime.now(timezone.utc).isoformat(),'checks':{}}

    try:
        save('preflight')
        assert git('rev-parse', 'HEAD') == PARENT, 'Core revision changed; re-review required'
        assert not (ROOT / '.deploy.lock').exists(), 'Core deployment lock is present'
        processes = run(['ps', '-eo', 'args']).splitlines()
        assert not any(re.search(r'(^|[/\s])deploy-prod[.]sh(?:\s|$)|docker compose.*build|rsync.*/opt/omnia', p)
                       for p in processes), 'Another Core deploy/build/rsync is active'
        # Observations only: status never begins, repairs or ends a fence/lease/run.
        pre_status = json.loads(run(['docker', 'exec', 'yleum-prod-api', 'python', '-m',
            'yleum_api.services.generation_deployment_drain', 'status', PARENT]))
        state['checks']['preDrainStatus'] = pre_status
        require_idle_preflight(pre_status)
        old_health = json.loads(run(['curl', '-fsS', 'https://yleum.ru/api/health']))
        require_release_health(old_health, PARENT)
        state['checks']['oldLiveHealth'] = old_health
        # Feed the authorized token directly to git's credential stdin, never argv/logs.
        token = os.environ['GH_TOKEN']
        subprocess.run(['git', 'credential', 'approve'], cwd=ROOT, text=True, check=True,
                       input='protocol=https\nhost=github.com\nusername=x-access-token\npassword='+token+'\n\n',
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        del token
        git('fetch', '-q', 'origin')
        assert git('rev-parse', 'origin/main') == SHA, 'Main advanced; exact CI must be rechecked'
        ci = json.loads(subprocess.run(['gh', 'run', 'view', str(CI_RUN_ID), '--json',
            'headSha,status,conclusion'], cwd=ROOT, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=True).stdout)
        require_exact_ci(ci, SHA)
        state['checks']['ciRunId'] = CI_RUN_ID
        state['checks']['ciConclusion'] = ci['conclusion']
        canonical_source = run(['git', '-C', str(ROOT), 'show', SHA + ':infra/release/deploy-prod.sh'])
        require_canonical_order(canonical_source)
        expected_gateway_hash = hashlib.sha256(run(['git', '-C', str(ROOT), 'show',
            SHA + ':apps/llm-gateway/src/yleum_gateway/services/billing.py']).encode()).hexdigest()
        state['checks']['expectedGatewayBillingModuleSHA256'] = expected_gateway_hash
        touched = set(git('diff', '--name-only', PARENT, SHA).splitlines())
        core_dirty = set(git('diff', '--name-only').splitlines())
        core_untracked = set(git('ls-files', '--others', '--exclude-standard').splitlines())
        assert not git('diff', '--cached', '--name-only'), 'Core has staged changes'
        assert not touched.intersection(core_dirty | core_untracked), 'Core candidate path collision'
        assert not effective_code_dirty(core_dirty | core_untracked), 'Core effective code changed'
        commerce = json.loads(remote(INVENTORY))
        assert commerce['head'] == PARENT, 'Commerce revision changed; re-review required'
        assert commerce['controllerHealth'] == {'status':'ok','release_sha':PARENT}, 'Commerce controller revision mismatch'
        assert not commerce['staged'], 'Commerce has staged changes'
        assert not commerce['activeDeployers'], 'Another Commerce deployment is active'
        if commerce['lockPresent']:
            assert commerce.get('lockRecognized'), 'Unrecognized Commerce lock; inspect owner first'
            assert commerce.get('lockRevision') == HISTORICAL_COMMERCE_LOCK_SHA, 'Commerce lock owner changed'
            assert commerce.get('lockTimestamp') == HISTORICAL_COMMERCE_LOCK_TIME, 'Commerce lock timestamp changed'
            git('merge-base', '--is-ancestor', HISTORICAL_COMMERCE_LOCK_SHA, PARENT)
            state['checks']['commerceLockClassification'] = 'Historical Sep29 release superseded by verified Oct1 release; no active deploy/build/rsync; preserved unchanged'
        protected = set(commerce['dirty'])
        assert not effective_code_dirty(protected), 'Commerce effective code changed'
        assert not touched.intersection(protected), 'Commerce candidate path collision'
        state['checks']['candidatePaths'] = len(touched)
        state['checks']['coreProtectedPaths'] = len(core_dirty | core_untracked)
        state['checks']['commerceProtectedPaths'] = len(protected)
        state['checks']['commerceLockPresent'] = commerce['lockPresent']
        state['checks']['commerceLockRecognized'] = commerce.get('lockRecognized')
        # Exact canonical rsync, checksum dry run. Keep detailed path evidence on Core.
        args = ['rsync', '-anic', '--out-format=%i %n', '--exclude', '/.deploy.lock*',
                '--exclude', '.venv', '--exclude', '.env', '--exclude', '.env.before-*',
                '--exclude', 'node_modules', '--exclude', '.next', '--exclude', '__pycache__',
                '--exclude', '*.tsbuildinfo', str(ROOT)+'/', 'commerce:'+str(ROOT)+'/']
        changes = run(args)
        (receipt / 'rsync-dryrun.log').write_text(changes)
        overwritten = []
        for line in changes.splitlines():
            assert re.match(r'^[<>ch.][fdLDS][A-Za-z+?.]{9} ', line), 'Unparsed rsync item; manual review required'
            flags, name = line[:11], line[12:]
            name = name.rstrip('/')
            overlaps = any(name == p or p.startswith(name+'/') or name.startswith(p+'/') for p in protected)
            if flags[1:2] == 'd' and flags == '.d..t......':
                continue
            if overlaps:
                overwritten.append(name)
        assert not overwritten, 'Canonical rsync would overwrite protected Commerce files'
        state['checks']['protectedRsyncOverwrites'] = 0
        save('fresh-encrypted-backup')
        with open(receipt / 'backup.log', 'w') as log:
            code = subprocess.call(['bash', str(ROOT/'infra/backup/backup-omnia.sh')], stdout=log, stderr=log)
        state['checks']['backupExitCode'] = code
        assert code == 0, 'Fresh backup failed; inspect protected Core backup log'
        # Execute exact candidate bytes, retaining the canonical $0/REPO and fence order.
        # Do not checkout candidate source before the canonical ingress/drain steps.
        canonical_path = receipt / ('canonical-' + SHA + '.sh')
        canonical_path.write_text(canonical_source)
        canonical_path.chmod(0o600)
        state['checks']['canonicalSourceSHA256'] = hashlib.sha256(canonical_path.read_bytes()).hexdigest()
        save('canonical-deployment')
        with open(receipt / 'deploy.log', 'w') as log:
            code = subprocess.call(['bash', '-c', 'canonical="$1"; shift; source "$canonical"',
                                    str(ROOT/'infra/release/deploy-prod.sh'), str(canonical_path),
                                    SHA, '--gateway'],
                                   cwd=ROOT, stdout=log, stderr=log)
        state['checks']['canonicalExitCode'] = code
        assert code == 0, 'Canonical deployment failed; inspect protected Core deployment log'
        assert git('rev-parse', 'HEAD') == SHA, 'Core checkout does not match candidate'
        health = json.loads(run(['curl', '-fsS', 'https://yleum.ru/api/health']))
        require_release_health(health, SHA)
        state['checks']['publicHealth'] = health
        web_health = json.loads(run(['curl','-fsS','--max-time','15','https://yleum.ru/web-health']))
        require_web_health(web_health, SHA)
        state['checks']['webHealth'] = web_health
        core_controller = json.loads(run(['curl', '-fsS', 'http://127.0.0.1:8003/health']))
        assert core_controller == {'status': 'ok', 'release_sha': SHA}, 'Core controller runtime mismatch'
        post_commerce = json.loads(remote(INVENTORY))
        assert post_commerce['head'] == SHA, 'Commerce checkout does not match candidate'
        assert post_commerce['controllerHealth'] == {'status': 'ok', 'release_sha': SHA}, 'Commerce controller runtime mismatch'
        assert not post_commerce['activeDeployers'], 'Another Commerce deployment is active after release'
        if commerce['lockPresent']:
            assert (post_commerce['lockPresent'] and post_commerce.get('lockRecognized')
                    and post_commerce.get('lockRevision') == HISTORICAL_COMMERCE_LOCK_SHA
                    and post_commerce.get('lockTimestamp') == HISTORICAL_COMMERCE_LOCK_TIME), 'Preserved Commerce lock changed'
        state['checks']['coreControllerHealth'] = core_controller
        state['checks']['commerceControllerHealth'] = post_commerce['controllerHealth']
        module_hash = run(['docker', 'exec', 'yleum-prod-gw', 'python', '-c',
            'import hashlib,importlib.util,pathlib; s=importlib.util.find_spec("yleum_gateway.services.billing"); assert s and s.origin; print(hashlib.sha256(pathlib.Path(s.origin).read_bytes()).hexdigest())']).strip()
        assert module_hash == expected_gateway_hash, 'Actual gateway billing module differs from candidate'
        state['checks']['gatewayBillingModuleSHA256'] = module_hash
        post_status = json.loads(run(['docker', 'exec', 'yleum-prod-api', 'python', '-m',
            'yleum_api.services.generation_deployment_drain', 'status', SHA]))
        require_post_release_status(post_status)
        ingress_status = json.loads(run(['sudo','python3','-'], data=INGRESS_POST))
        require_ingress_absent(ingress_status)
        state['checks']['postIngressStatus'] = ingress_status
        state['checks']['postDrainStatus'] = post_status
        save('complete')
        return 0
    except Exception as exc:
        state['errorType'] = type(exc).__name__
        # Exception messages are local guard labels only; subprocess stderr stays private.
        if isinstance(exc, AssertionError):
            state['guardFailure'] = str(exc)
        save('blocked')
        raise SystemExit(1)

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'phase':'blocked','errorType':type(error).__name__}))
        raise SystemExit(1) from None
