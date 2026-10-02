"""Load one proven immutable image on normal hosts; never change pins or start apps."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import subprocess
import urllib.request

IMAGE = 'sha256:cef537458d384b32a717fb3bea6361d76f29f4db73d8728c2b32e96e6740cfca'
ARCHIVE_SHA = '8e5b676d06783bee25da8bbeabe4f5d2bb135d943601bcc3aa7df7af4bbda4ff'
DEST = Path('/tmp/qa-action-core-cef537-stage')
ENV = Path('/opt/omnia/apps/orchestrator/.env')

def command(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE).strip()

def pins():
    return [line for line in ENV.read_text().splitlines()
            if line.startswith(('CELL_PREVIEW_CORE_IMAGE=', 'CELL_PUBLIC_CORE_IMAGE='))]

def load(manifest, archive, state):
    assert archive.is_file() and not archive.is_symlink()
    assert archive.stat().st_size == manifest['archive_bytes'] == 115580327
    with archive.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == ARCHIVE_SHA
    prior = pins()
    assert len(prior) == 2, 'Both prior pins must exist'
    state['prior_pins'] = prior
    with (DEST / 'docker-load.log').open('w') as log:
        result = subprocess.run(['docker', 'load', '--input', str(archive)],
                                stdout=log, stderr=subprocess.STDOUT)
    state['load_exit'] = result.returncode
    assert result.returncode == 0, 'Image load failed'
    actual = json.loads(command(['docker', 'image', 'inspect', IMAGE]))[0]
    assert actual['Id'] == IMAGE
    labels = actual['Config'].get('Labels', {})
    assert all(labels.get(key) == value for key, value in manifest['protocols'].items())
    assert actual['Config']['Cmd'] == ['node', 'server.js']
    assert actual['Config'].get('User') == 'node'
    assert pins() == prior, 'Image staging changed pins'
    state.update(image_id=IMAGE, protocols_verified=True, pins_unchanged=True,
                 containers_started_by_helper=0, phase='image-staged')

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-core', action='store_true')
    parser.add_argument('--execute-local', action='store_true')
    parser.add_argument('--manifest')
    parser.add_argument('--manifest-sha')
    parser.add_argument('--both-hosts', action='store_true')
    args = parser.parse_args(argv)
    if not args.execute_core and not args.execute_local:
        print(json.dumps({'phase': 'prepared-only', 'image_id': IMAGE, 'pins_changed': False}))
        return 0
    assert args.execute_core != args.execute_local
    assert pwd.getpwuid(os.geteuid()).pw_name == 'zeuszcz'
    os.umask(0o077)
    source = Path(args.manifest)
    assert source.is_file() and not source.is_symlink()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == args.manifest_sha
    manifest = json.loads(source.read_text())
    assert manifest['complete'] is True and manifest['image_id'] == IMAGE
    assert manifest['archive_sha256'] == ARCHIVE_SHA
    assert len(manifest['chunks']) == 28
    assert [chunk['index'] for chunk in manifest['chunks']] == list(range(28))
    if args.execute_core:
        DEST.mkdir(mode=0o700)  # Exclusive receipt protects unknown prior outcomes.
    else:
        assert DEST.is_dir() and not DEST.is_symlink()
        assert not (DEST / 'state.json').exists(), 'Local receipt already exists'
    state = {'phase': 'staging', 'pins_changed': False, 'image_id': IMAGE}
    archive = DEST / 'core.docker.tar.gz'
    try:
        if args.execute_core:
            token = os.environ['GH_TOKEN']
            with archive.open('xb') as stream:
                for chunk in manifest['chunks']:
                    assert 0 < chunk['bytes'] <= 4194304
                    url = ('https://api.github.com/repos/Artem-Levchenko-hub/'
                           'ConstrucorsitesAI/git/blobs/' + chunk['git_blob'])
                    request = urllib.request.Request(url,
                        headers={'Authorization': 'Bearer ' + token,
                                 'Accept': 'application/vnd.github+json'})
                    response = json.load(urllib.request.urlopen(request, timeout=30))
                    assert response['sha'] == chunk['git_blob'] and response['encoding'] == 'base64'
                    payload = base64.b64decode(response['content'])
                    assert len(payload) == chunk['bytes']
                    assert hashlib.sha256(payload).hexdigest() == chunk['sha256']
                    expected_git = hashlib.sha1(b'blob ' + str(len(payload)).encode()
                                                + b'\0' + payload).hexdigest()
                    assert expected_git == chunk['git_blob']
                    stream.write(payload)
            del token
        load(manifest, archive, state)
        if args.both_hosts:
            assert args.execute_core
            remote_init = "import pathlib,os;os.umask(0o077);pathlib.Path('/tmp/qa-action-core-cef537-stage').mkdir(mode=0o700)"
            subprocess.run(['ssh', '-o', 'BatchMode=yes', 'commerce', 'python3', '-'],
                           input=remote_init, text=True, capture_output=True, check=True)
            target = 'commerce:' + str(DEST) + '/'
            for local in (archive, source, Path(__file__)):
                subprocess.run(['scp', '-q', str(local), target], capture_output=True, check=True)
            remote_args = ['ssh', '-o', 'BatchMode=yes', 'commerce', 'python3',
                str(DEST / Path(__file__).name), '--execute-local', '--manifest',
                str(DEST / source.name), '--manifest-sha', args.manifest_sha]
            state['commerce_result'] = json.loads(command(remote_args))
            assert state['commerce_result']['phase'] == 'image-staged'
            state['both_hosts_image_staged'] = True
    except Exception as error:
        state.update(phase='blocked', error_type=type(error).__name__)
        if isinstance(error, AssertionError):
            state['guard_failure'] = str(error)
    finally:
        (DEST / 'state.json').write_text(json.dumps(state, indent=2) + '\n')
    safe = {key: value for key, value in state.items() if key != 'prior_pins'}
    print(json.dumps(safe))
    return 0 if state['phase'] == 'image-staged' else 1

if __name__ == '__main__':
    raise SystemExit(main())
