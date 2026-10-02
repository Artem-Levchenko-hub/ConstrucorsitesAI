#!/usr/bin/env python3
"""Prepare, or explicitly execute two anonymous TLS probes in an owned QA core.

No deployment, Env/log/data reads, provider POST, global CA changes or file writes
inside the core. Root alone supplies reviewed prior-inventory identity and runs it.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
from uuid import UUID

CA_SHA256 = 'aa800ef345422d6158c6fafe1c06c429dbda21c3df4bb1ccb45a920ec1111399'
DOCKER = ['docker', '--host', 'unix:///var/run/docker.sock']
ENDPOINT = 'https://platform-api2.max.ru/me'
KNOWN_CODES = {
    'UNABLE_TO_VERIFY_LEAF_SIGNATURE', 'UNABLE_TO_GET_ISSUER_CERT_LOCALLY',
    'UNABLE_TO_GET_ISSUER_CERT', 'DEPTH_ZERO_SELF_SIGNED_CERT',
    'SELF_SIGNED_CERT_IN_CHAIN', 'CERT_HAS_EXPIRED', 'CERT_NOT_YET_VALID',
    'CERT_SIGNATURE_FAILURE', 'ERR_TLS_CERT_ALTNAME_INVALID',
    'ERR_TLS_CERT_SIGNATURE_ALGORITHM_UNSUPPORTED', 'ECONNREFUSED', 'ECONNRESET',
    'ENOTFOUND', 'EAI_AGAIN', 'ENETUNREACH', 'EHOSTUNREACH', 'ETIMEDOUT',
    'UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_SOCKET', 'TIMEOUT',
    'UNCLASSIFIED_NETWORK_ERROR',
}

NODE_PROBE = r'''
const https = require('node:https');
const tls = require('node:tls');
const crypto = require('node:crypto');
const endpoint = 'https://platform-api2.max.ru/me';
const caHash = 'aa800ef345422d6158c6fafe1c06c429dbda21c3df4bb1ccb45a920ec1111399';
const allowed = new Set(CODES_PLACEHOLDER);
function errorResult(e) {
  let code = e?.cause?.code || e?.code;
  if (e?.name === 'TimeoutError' || e?.name === 'AbortError') code = 'TIMEOUT';
  return {error_code: allowed.has(code) ? code : 'UNCLASSIFIED_NETWORK_ERROR'};
}
async function nativeProbe() {
  try {
    const response = await fetch(endpoint, {method: 'GET', redirect: 'error',
      signal: AbortSignal.timeout(10000)});
    const result = {status: response.status};
    if (response.body) await response.body.cancel().catch(() => {});
    return result;
  } catch (e) { return errorResult(e); }
}
function scopedProbe(ca) {
  return new Promise(resolve => {
    let done = false;
    let timer;
    const finish = result => { if (!done) { done = true; clearTimeout(timer); resolve(result); } };
    try {
      const request = https.get(endpoint, {ca: [...tls.rootCertificates, ca],
        rejectUnauthorized: true, agent: false}, response => {
          finish({status: response.statusCode});
          response.destroy();
        });
      request.on('error', e => finish(errorResult(e)));
      timer = setTimeout(() => {
        const e = new Error(); e.code = 'ETIMEDOUT'; request.destroy(e);
        finish({error_code: 'ETIMEDOUT'});
      }, 10000);
    } catch (e) { finish(errorResult(e)); }
  });
}
let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  raw += chunk;
  if (raw.length > 32768) { console.log(JSON.stringify({phase:'blocked',error_code:'INPUT_LIMIT'})); process.exit(1); }
});
process.stdin.on('end', async () => {
  try {
    const input = JSON.parse(raw);
    const ca = input.ca_pem;
    if (typeof ca !== 'string' || crypto.createHash('sha256').update(ca).digest('hex') !== caHash) {
      console.log(JSON.stringify({phase:'blocked',error_code:'CA_HASH_MISMATCH'})); process.exitCode = 1; return;
    }
    const native = await nativeProbe();
    const scoped = await scopedProbe(ca);
    console.log(JSON.stringify({phase:'probed',node_version:process.version,native,scoped}));
  } catch (_) { console.log(JSON.stringify({phase:'blocked',error_code:'PROBE_INPUT_OR_RUNTIME'})); process.exitCode = 1; }
});
'''.replace('CODES_PLACEHOLDER', json.dumps(sorted(KNOWN_CODES)))


class Rejected(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Rejected(code)


def identity(args):
    require(re.fullmatch(r'[0-9a-f]{64}', args.container), 'immutable_container_id')
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', args.image), 'immutable_image_id')
    return {key: str(UUID(getattr(args, key))) for key in ('owner', 'project', 'workspace')}


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    require(result.returncode == 0, 'runtime_command_failed')
    require(len(result.stdout) < 65536, 'runtime_output_limit')
    return json.loads(result.stdout)


def inspect_owned(args, invoke=run):
    ids = identity(args)
    template = ('{"id":{{json .Id}},"image":{{json .Image}},"status":{{json .State.Status}},'
                '"labels":{{json .Config.Labels}}}')
    item = invoke([*DOCKER, 'container', 'inspect', '--format', template, args.container], timeout=15)
    expected = {'omnia.managed': 'true', 'omnia.project_machine': 'true',
                'omnia.namespace': 'prod', 'omnia.resource_kind': 'managed-max-core',
                'omnia.max-core.protocol': '1', 'omnia.max-core.db-role-protocol': '1',
                **{'omnia.' + key + '_id': value for key, value in ids.items()}}
    require(item.get('id') == args.container and item.get('image') == args.image,
            'container_image_changed')
    require(item.get('status') == 'running', 'core_not_running')
    require(all((item.get('labels') or {}).get(k) == v for k, v in expected.items()),
            'core_ownership_protocol')
    image = invoke([*DOCKER, 'image', 'inspect', '--format',
                    '{"id":{{json .Id}},"labels":{{json .Config.Labels}}}', args.image], timeout=15)
    require(image.get('id') == args.image and
            (image.get('labels') or {}).get('omnia.max-core.protocol') == '1',
            'image_protocol_changed')
    return ids


def read_public_ca(path):
    path = Path(path)
    require(path.is_absolute() and all(not p.is_symlink() for p in [path, *path.parents]),
            'unsafe_public_ca_path')
    require(stat.S_ISREG(path.stat().st_mode) and path.stat().st_size < 32768,
            'unsafe_public_ca_file')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == CA_SHA256, 'public_ca_hash')
    return raw.decode('ascii')


def prior_inventory(args):
    path = Path(args.prior_inventory)
    require(path.is_absolute() and all(not p.is_symlink() for p in [path, *path.parents]),
            'unsafe_prior_inventory_path')
    require(stat.S_ISREG(path.stat().st_mode) and path.stat().st_size < 65536,
            'unsafe_prior_inventory_file')
    data = json.loads(path.read_bytes())
    rows = data.get('owner_scoped_cores')
    require(isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict),
            'prior_inventory_single_owned_core')
    row = rows[0]
    require(all(row.get(k) == value for k, value in {
        'id': args.container, 'image': args.image, 'project': str(UUID(args.project)),
        'workspace': str(UUID(args.workspace))}.items()), 'prior_inventory_binding')
    # The collector intentionally removes owner from scoped rows. Explicit owner
    # is checked against fresh trusted container labels, never inferred here.


def clean_result(result):
    require(isinstance(result, dict) and set(result) ==
            {'phase', 'node_version', 'native', 'scoped'} and result['phase'] == 'probed',
            'probe_result_shape')
    require(re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', result['node_version']), 'node_version_shape')
    for name in ('native', 'scoped'):
        row = result[name]
        require(isinstance(row, dict), 'probe_status_shape')
        require((set(row) == {'status'} and type(row['status']) is int and
                 100 <= row['status'] <= 599) or
                (set(row) == {'error_code'} and row['error_code'] in KNOWN_CODES),
                'probe_status_shape')
    return result


def execute(args, invoke=run, read_ca=read_public_ca, check_prior=prior_inventory):
    identity(args)
    check_prior(args)
    ca = read_ca(args.public_ca)
    inspect_owned(args, invoke)
    result = invoke([*DOCKER, 'exec', '-i', args.container, 'node', '-e', NODE_PROBE],
                    input=json.dumps({'ca_pem': ca}), timeout=30)
    result = clean_result(result)
    inspect_owned(args, invoke)
    return {'phase': 'owned_qa_tls_probed', 'binding_verified_before_and_after': True,
            'node_version': result['node_version'], 'native': result['native'],
            'scoped': result['scoped'], 'public_ca_sha256': CA_SHA256,
            'provider_body_consumed': False, 'par_b_root_cause_proved': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('container', 'image', 'owner', 'project', 'workspace', 'public-ca',
                 'prior-inventory'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    try:
        identity(args)
        result = execute(args) if args.execute else {'phase': 'prepared', 'effects': 0,
                                                     'runtime_reads': False, 'network_calls': 0}
        print(json.dumps(result, sort_keys=True))
    except Exception as error:
        print(json.dumps({'phase': 'blocked', 'error_code': str(error)
                          if isinstance(error, Rejected) else type(error).__name__}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
