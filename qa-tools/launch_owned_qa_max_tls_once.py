#!/usr/bin/env python3
"""Root-only once-budget launcher for the reviewed original QA-B TLS probe."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from uuid import UUID

HELPER = Path('/tmp/qa-owned-max-tls-probe.py')
HELPER_SHA = '617e01c53dcd4c861fffd12c18b7719a886e41269093ba5ab5e8668198b5ffa7'
PRIOR = Path('/tmp/Iv-core.json')
PRIOR_SHA = '40aa7e9aba4bd3edfb40e0128fb276486f6ed07b4a099aa19335f9ea18bdac42'
OWNER_SHA = '96e57ea71681b6e0548b0a802d30bf417ad369a9350ea55aa90c23d5189649df'
PUBLIC_CA = '/opt/omnia/apps/api/src/yleum_api/certs/russian_trusted_root_ca.pem'
MARKER = Path('/tmp/qa-owned-max-tls-617e-once.json')
RESULT = Path('/tmp/qa-owned-max-tls-617e-result.json')


class Blocked(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Blocked(code)


def verified_bytes(path, expected):
    require(path.is_absolute() and all(not p.is_symlink() for p in [path, *path.parents]),
            'unsafe_pinned_path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        metadata = os.fstat(stream.fileno())
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_size < 65536,
                'unsafe_pinned_file')
        raw = stream.read(65536)
    require(hashlib.sha256(raw).hexdigest() == expected, 'pinned_hash_mismatch')
    return raw


def resolve_args(data):
    require(isinstance(data, dict) and data.get('schema') == 'omnia-action-caller-inventory-v1'
            and data.get('mode') == 'host', 'prior_inventory_schema')
    owner = data.get('owner_scope')
    require(isinstance(owner, str) and str(UUID(owner)) == owner and
            hashlib.sha256(owner.encode('ascii')).hexdigest() == OWNER_SHA,
            'owner_scope_hash')
    rows = data.get('owner_scoped_cores')
    require(isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict),
            'prior_single_owned_core')
    row = rows[0]
    return SimpleNamespace(owner=owner, project=row['project'], workspace=row['workspace'],
                           container=row['id'], image=row['image'], public_ca=PUBLIC_CA,
                           prior_inventory=str(PRIOR))


def exclusive(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    return os.fdopen(fd, 'w', encoding='ascii')


def execute():
    raw = verified_bytes(HELPER, HELPER_SHA)
    args = resolve_args(json.loads(verified_bytes(PRIOR, PRIOR_SHA)))
    # Execute exact reviewed bytes without import cache or protected-container writes.
    scope = {'__name__': 'reviewed_owned_qa_tls', '__file__': str(HELPER)}
    exec(compile(raw, str(HELPER), 'exec'), scope)
    scope['identity'](args)
    # Both private files must be absent. No retry/resume path or marker deletion.
    with exclusive(MARKER) as marker:
        marker.write(json.dumps({'phase': 'claimed_once', 'helper_sha256': HELPER_SHA,
                                 'prior_sha256': PRIOR_SHA}) + '\n')
        marker.flush()
        os.fsync(marker.fileno())
        with exclusive(RESULT) as result_file:
            try:
                result = scope['execute'](args)
            except Exception as error:
                # Leave the claim in place even if no GET was reached.
                result = {'phase': 'blocked', 'error_class': type(error).__name__}
            result_file.write(json.dumps(result, sort_keys=True) + '\n')
            result_file.flush()
            os.fsync(result_file.fileno())
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get('phase') == 'owned_qa_tls_probed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'phase': 'prepared', 'effects': 0, 'reads': 0}))
        return 0
    try:
        return execute()
    except Exception as error:
        print(json.dumps({'phase': 'blocked', 'error_code': str(error)
                          if isinstance(error, Blocked) else type(error).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
