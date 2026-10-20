"""Execute the actual installed shell payload; no requests leave the test stub."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux watchdog shell contract")
SOURCE = Path(__file__).resolve().parents[3] / "infra/max-k3s/remote/80-uptime-watchdog.sh"


@pytest.fixture
def watchdog(tmp_path):
    source = SOURCE.read_text()
    payload = source.split('cat > "$BIN" <<\'EOF\'\n', 1)[1].split("\nEOF", 1)[0]
    script = tmp_path / "watchdog.sh"
    script.write_text(payload)
    commands = tmp_path / "commands"
    commands.mkdir()
    curl = commands / "curl"
    curl.write_text(f"#!{sys.executable}\n" + r'''
import datetime,json,os,pathlib,sys
args=sys.argv[1:]
url=next(a for a in args if a.startswith('https://'))
if 'telegram.org' in url:
    log=pathlib.Path(os.environ['CALL_LOG'])
    with log.open('a') as f:f.write('notification\n')
    mode=os.environ['DELIVERY']
    body={'ok':mode=='ok'}
    status=500 if mode=='http500' else 200
elif '/backups/' in url:
    now=datetime.datetime.now(datetime.timezone.utc)
    mode=os.environ.get('BACKUP','ok')
    created=now-datetime.timedelta(hours=40 if mode=='stale' else 1)
    body={'created_at':created.isoformat(),'restore_test_ok':mode!='unverified',
          'restore_tested_at':now.isoformat()}
    status=200
else:
    status=int(os.environ.get('API_STATUS' if '/api/health' in url else 'WEB_STATUS','200'))
    body={}
if '--output' in args:
    pathlib.Path(args[args.index('--output')+1]).write_text(json.dumps(body))
if '%{http_code}' in args:print(status,end='')
if '--fail' in args and status>=400:raise SystemExit(22)
''')
    curl.chmod(0o700)
    logger = commands / "logger"
    logger.write_text("#!/bin/sh\nexit 0\n")
    logger.chmod(0o700)
    state = tmp_path / "state"
    calls = tmp_path / "calls"
    env = {
        **os.environ, "PATH": str(commands) + os.pathsep + os.environ["PATH"],
        "WATCHDOG_ENV_FILE": str(tmp_path / "absent.env"),
        "WATCHDOG_STATE_DIR": str(state), "WATCHDOG_THRESHOLD": "1",
        "WATCHDOG_URL": "https://synthetic.invalid/api/health",
        "WATCHDOG_WEB_URL": "https://synthetic.invalid/",
        "WATCHDOG_BACKUP_STATUS_URL": "https://synthetic.invalid/api/backups/offhost/status",
        "WATCHDOG_BACKUP_READ_TOKEN": "synthetic-reader",
        "TG_BOT_TOKEN": "synthetic-bot", "TG_CHAT_ID": "synthetic-recipient",
        "CALL_LOG": str(calls), "DELIVERY": "ok",
    }

    def run(**changes):
        return subprocess.run([shutil.which("bash"), str(script)], env={**env, **changes},
                              capture_output=True, text=True, timeout=10)

    return run, state, calls


@pytest.mark.parametrize("delivery", ["http500", "false"])
@pytest.mark.parametrize("optimized", ["", "1"])
def test_failed_delivery_is_not_acknowledged_and_retries(watchdog, delivery, optimized):
    run, state, calls = watchdog
    for attempt in range(2):
        result = run(API_STATUS="503", DELIVERY=delivery, PYTHONOPTIMIZE=optimized)
        assert result.returncode == 1
        assert not (state / "alerted").exists()
        assert len(calls.read_text().splitlines()) == attempt + 1


def test_recovery_remains_pending_until_delivery_is_confirmed(watchdog):
    run, state, calls = watchdog
    assert run(API_STATUS="503").returncode == 1
    assert (state / "alerted").exists()
    assert run(API_STATUS="503").returncode == 1
    assert len(calls.read_text().splitlines()) == 1
    assert run(DELIVERY="false").returncode == 1
    assert (state / "alerted").exists()
    assert run().returncode == 0
    assert not (state / "alerted").exists()
    assert not (state / "fails").exists()
    assert len(calls.read_text().splitlines()) == 3
    assert run().returncode == 0
    assert len(calls.read_text().splitlines()) == 3


@pytest.mark.parametrize("backup", ["stale", "unverified"])
@pytest.mark.parametrize("optimized", ["", "1"])
def test_healthy_site_does_not_hide_failed_backup(watchdog, backup, optimized):
    run, state, _calls = watchdog
    assert run(BACKUP=backup, PYTHONOPTIMIZE=optimized).returncode == 1
    assert (state / "alerted").exists()


def test_missing_delivery_settings_never_mark_an_alert_sent(watchdog):
    run, state, calls = watchdog
    assert run(API_STATUS="503", TG_BOT_TOKEN="", TG_CHAT_ID="").returncode == 1
    assert not (state / "alerted").exists()
    assert not calls.exists()


def test_missing_backup_credential_is_not_a_healthy_backup(watchdog):
    run, state, _calls = watchdog
    assert run(WATCHDOG_BACKUP_READ_TOKEN="").returncode == 1
    assert (state / "alerted").exists()


def test_a_skipped_concurrent_probe_never_reports_health_success(watchdog):
    import fcntl

    run, state, calls = watchdog
    state.mkdir()
    with (state / "lock").open("w") as locked:
        fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run(API_STATUS="503").returncode == 75
    assert not calls.exists()
    assert not (state / "fails").exists()
