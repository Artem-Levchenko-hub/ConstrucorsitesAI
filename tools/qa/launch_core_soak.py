import hashlib
import os
from pathlib import Path
import subprocess

base = Path('/opt/omnia-runtime/qa-observe-20261001')
helper = base / 'core_readonly_soak.py'
expected_helper = '8ef79748ac413bc385d451fb8e8066be0f3dce6afd6164d3022fb723d3c82c6c'
if hashlib.sha256(helper.read_bytes()).hexdigest() != expected_helper:
    raise RuntimeError('Observer helper checksum mismatch; launch refused')
os.umask(0o077)
allowed = {'PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ',
           'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
           'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
           'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE'}
clean_env = {key: value for key, value in os.environ.items() if key in allowed}
clean_env.setdefault('PATH', '/usr/local/bin:/usr/bin:/bin')
# The allowlist drops ALL arbitrary credentials, including GH_TOKEN,
# GITHUB_TOKEN, QA_* passwords/emails/cookies, SSHPASS and browser secrets.
fd = os.open(base / 'observer-launch.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
os.fchmod(fd, 0o600)
with os.fdopen(fd, 'ab', buffering=0) as log:
    process = subprocess.Popen(
        ['timeout', '--signal=TERM', '--kill-after=5s', '86455s',
         'python3', str(helper), '--run', '--sudo-docker',
         '--expected-revision', 'a3b7bb0147d116626d16ea8e9180088217a912b9'],
        env=clean_env, stdin=subprocess.DEVNULL, stdout=log,
        stderr=subprocess.STDOUT, start_new_session=True, cwd='/')
print('Read-only observer wrapper PID:', process.pid)
