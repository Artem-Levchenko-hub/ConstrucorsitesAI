#!/usr/bin/env python3
"""Diagnose frozen QA TLS preflight guards. No Node, HTTP or once-state access."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

LAUNCHER = Path('/tmp/qa-owned-max-tls-launch.py')
LAUNCHER_SHA = '69a53d80239045c0f816e423b2d7d9a45c23aa3acfc4f16038a7ddfa38b4933f'
HELPER_SHA = '617e01c53dcd4c861fffd12c18b7719a886e41269093ba5ab5e8668198b5ffa7'
CODES = set('''OK unsafe_pinned_path unsafe_pinned_file pinned_hash_mismatch
prior_inventory_schema owner_scope_hash prior_single_owned_core
immutable_container_id immutable_image_id unsafe_prior_inventory_path
unsafe_prior_inventory_file prior_inventory_single_owned_core prior_inventory_binding
unsafe_public_ca_path unsafe_public_ca_file public_ca_hash container_image_changed
core_not_running core_ownership_protocol image_protocol_changed runtime_command_failed
runtime_output_limit core_managed_mismatch core_machine_mismatch core_namespace_mismatch
core_kind_mismatch core_public_protocol_mismatch core_db_protocol_mismatch
core_owner_mismatch core_project_mismatch core_workspace_mismatch
diagnostic_command_rejected'''.split())


class DiagnosticBlocked(Exception):
    pass


def scope_from(path, expected):
    if not path.is_absolute() or any(p.is_symlink() for p in [path, *path.parents]):
        raise DiagnosticBlocked('unsafe_pinned_path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size >= 65536:
            raise DiagnosticBlocked('unsafe_pinned_file')
        raw = stream.read(65536)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise DiagnosticBlocked('pinned_hash_mismatch')
    scope = {'__name__': 'readonly_guard_diagnosis', '__file__': str(path)}
    exec(compile(raw, str(path), 'exec'), scope)
    # No retained entrypoint capable of a TLS probe, marker creation or Node exec.
    scope.pop('execute', None)
    scope.pop('main', None)
    scope.pop('exclusive', None)
    scope.pop('NODE_PROBE', None)
    return scope


def error_code(error):
    text = str(error)
    if text in CODES:
        return text
    if isinstance(error, FileNotFoundError):
        return 'FILE_NOT_FOUND'
    if isinstance(error, PermissionError):
        return 'PERMISSION_DENIED'
    if isinstance(error, json.JSONDecodeError):
        return 'JSON_INVALID'
    return 'VALIDATION_ERROR'


def inspect_only(helper, args):
    seen = {}

    def invoke(command, **kwargs):
        kind = command[3] if len(command) == 8 else ''
        target = args.container if kind == 'container' else args.image
        if (kind not in ('container', 'image') or command[:3] != helper['DOCKER']
                or command[4:6] != ['inspect', '--format'] or command[-1] != target
                or any(x in command[6] for x in ('.Env', '.Args', '.Cmd', '.Mounts'))
                or kwargs != {'timeout': 15}):
            raise DiagnosticBlocked('diagnostic_command_rejected')
        value = helper['run'](command, **kwargs)
        seen[kind] = value
        return value

    try:
        helper['inspect_owned'](args, invoke)
    except Exception as error:
        if str(error) == 'core_ownership_protocol' and 'container' in seen:
            labels = seen['container'].get('labels') or {}
            fields = (
                ('omnia.managed', 'true', 'core_managed_mismatch'),
                ('omnia.project_machine', 'true', 'core_machine_mismatch'),
                ('omnia.namespace', 'prod', 'core_namespace_mismatch'),
                ('omnia.resource_kind', 'managed-max-core', 'core_kind_mismatch'),
                ('omnia.max-core.protocol', '1', 'core_public_protocol_mismatch'),
                ('omnia.max-core.db-role-protocol', '1', 'core_db_protocol_mismatch'),
                ('omnia.owner_id', args.owner, 'core_owner_mismatch'),
                ('omnia.project_id', args.project, 'core_project_mismatch'),
                ('omnia.workspace_id', args.workspace, 'core_workspace_mismatch'),
            )
            for key, expected, code in fields:
                if labels.get(key) != expected:
                    raise DiagnosticBlocked(code) from None
        raise


def check():
    def step(stage, operation):
        try:
            result = operation()
            print(json.dumps({'stage': stage, 'error_code': 'OK'}))
            return True, result
        except Exception as error:
            print(json.dumps({'stage': stage, 'error_code': error_code(error)}))
            return False, None

    ok, launcher = step('launcher_pin', lambda: scope_from(LAUNCHER, LAUNCHER_SHA))
    if not ok:
        return 1
    ok, helper = step('helper_pin', lambda: scope_from(launcher['HELPER'], HELPER_SHA))
    if not ok:
        return 1
    ok, raw = step('prior_hash', lambda: launcher['verified_bytes'](
        launcher['PRIOR'], launcher['PRIOR_SHA']))
    if not ok:
        return 1
    ok, args = step('prior_owner_row', lambda: launcher['resolve_args'](json.loads(raw)))
    if not ok:
        return 1
    for stage, operation in (
        ('identity', lambda: helper['identity'](args)),
        ('prior_semantics', lambda: helper['prior_inventory'](args)),
    ):
        ok, _ = step(stage, operation)
        if not ok:
            return 1
    ca_ok, _ = step('public_ca', lambda: helper['read_public_ca'](args.public_ca))
    metadata_ok, _ = step('selected_inspect', lambda: inspect_only(helper, args))
    return 0 if ca_ok and metadata_ok else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    if not parser.parse_args().check:
        print(json.dumps({'stage': 'prepared', 'error_code': 'NOT_RUN'}))
        return 0
    return check()


if __name__ == '__main__':
    raise SystemExit(main())
