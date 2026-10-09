"""Execute canonical release shell blocks against isolated Git/Docker boundaries."""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "infra/release/deploy-prod.sh"
SHA = "a" * 40
BASH = shutil.which("bash") or shutil.which("sh")
pytestmark = pytest.mark.skipif(BASH is None, reason="Bash is required for release regressions")


def shell_path(path):
    # The bundled Windows sh is GNU Bash/MSYS; its shell paths use forward slashes.
    return str(path).replace("\\", "/")


def shell(code):
    return subprocess.run(
        [BASH, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        # Bash consumes UTF-8 paths. Its Python fixture children must not print
        # those paths using Windows cp1251, regardless of the pytest parent mode.
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        timeout=20,
    )


def capture_remote(tmp_path, line):
    capture = tmp_path / "remote-command"
    setup = (
        f"SHA={SHA}; API=1; WEB=0; "
        'ssh() { printf "%s" "$2" > ' + shlex.quote(shell_path(capture)) + "; }; "
    )
    result = shell(setup + line)
    assert result.returncode == 0, result.stderr
    return capture.read_text(encoding="utf-8")


@pytest.mark.parametrize("failure", ["fetch", "merge", "head"])
def test_revision_failure_stops_before_env_or_build(tmp_path, failure):
    line = next(
        line
        for line in SCRIPT.read_text(encoding="utf-8").splitlines()
        if "git fetch -q origin" in line
    )
    remote = capture_remote(tmp_path, line)
    compose = tmp_path / "apps/llm-gateway/deploy/full"
    compose.mkdir(parents=True)
    env_changed, build_started = tmp_path / "env-changed", tmp_path / "build-started"
    remote = remote.replace("/opt/omnia", shell_path(tmp_path))
    code = f"""
git() {{
  if [ "$1" = {failure} ]; then return 23; fi
  if [ "$1 $2" = 'rev-parse HEAD' ]; then
    if [ '{failure}' = head ]; then printf 'wrong\\n'; else printf '{SHA}\\n'; fi
  fi
}}
sed() {{ printf changed > {shlex.quote(shell_path(env_changed))}; }}
grep() {{ return 0; }}
{remote}
printf started > {shlex.quote(shell_path(build_started))}
"""
    result = shell(code)
    assert result.returncode != 0, "failed revision preparation continued toward rollout"
    assert not env_changed.exists(), "release identity changed despite failed revision preparation"
    assert not build_started.exists(), "build started despite failed revision preparation"


def test_verified_revision_can_prepare_release_env(tmp_path):
    line = next(
        line
        for line in SCRIPT.read_text(encoding="utf-8").splitlines()
        if "git fetch -q origin" in line
    )
    remote = capture_remote(tmp_path, line)
    (tmp_path / "apps/llm-gateway/deploy/full").mkdir(parents=True)
    changed = tmp_path / "env-changed"
    code = f"""
git() {{ if [ "$1 $2" = 'rev-parse HEAD' ]; then printf '{SHA}\\n'; fi; }}
sed() {{ printf changed > {shlex.quote(shell_path(changed))}; }}
grep() {{ return 0; }}
{remote.replace("/opt/omnia", shell_path(tmp_path))}
"""
    result = shell(code)
    assert result.returncode == 0, result.stderr
    assert changed.read_text() == "changed"


def run_build(tmp_path, *, build_exit=0, image_present=True, launch_lost=False):
    script = SCRIPT.read_text(encoding="utf-8")
    start = script.index('say "core: сборка $TARGETS')
    end = script.index("\nif [ $API = 1 ]; then", start)
    block = script[start:end]
    compose = tmp_path / "compose"
    compose.mkdir(exist_ok=True)
    events = tmp_path / "events"
    events.write_text("", encoding="utf-8")
    block = block.replace("/opt/omnia/apps/llm-gateway/deploy/full", shell_path(compose))
    code = f"""
set -euo pipefail
SHA={SHA}; API=1; WEB=0; TARGETS=api
LOG={shlex.quote(shell_path(tmp_path / "previous.log"))}
EVENTS={shlex.quote(shell_path(events))}; export EVENTS
BASH_BIN={shlex.quote(shell_path(Path(BASH)))}; export BASH_BIN
TEST_PYTHON={shlex.quote(shell_path(Path(sys.executable)))}; export TEST_PYTHON
say() {{ :; }}
seq() {{ local i; for ((i=$1; i<=$2; i++)); do printf '%s\\n' "$i"; done; }}
mktemp() {{ "$TEST_PYTHON" -c '
import sys, tempfile
print(tempfile.mkdtemp(dir=sys.argv[1],prefix="build-run-").replace(chr(92),"/"))
' {shlex.quote(shell_path(tmp_path))}; }}
sleep() {{ "$TEST_PYTHON" -c 'import time; time.sleep(0.01)'; }}
cat() {{ while IFS= read -r line; do printf '%s\\n' "$line"; done < "$1"; }}
mv() {{ "$TEST_PYTHON" -c 'import os,sys; os.replace(sys.argv[1],sys.argv[2])' "$@"; }}
tail() {{ return 0; }}
grep() {{ return 0; }}
pgrep() {{ return 1; }}
bash() {{ command "$BASH_BIN" "$@"; }}
nohup() {{ {"return 45" if launch_lost else '"$@"'}; }}
docker() {{
  if [[ "$*" == *' build api' ]]; then
    printf 'build-start\\n' >> "$EVENTS"
    "$TEST_PYTHON" -c 'import time; time.sleep(0.2)'
    printf 'build-end\\n' >> "$EVENTS"
    return {build_exit}
  elif [[ "$*" == 'image inspect '* ]]; then
    printf 'inspect\\n' >> "$EVENTS"
    return {0 if image_present else 1}
  fi
  return 99
}}
export -f docker bash nohup mv
ssh() {{ (eval "$2"); }}
{block}
printf 'rollout\\n' >> "$EVENTS"
"""
    result = shell(code)
    return result, events.read_text().splitlines() if events.exists() else []


def test_failed_current_build_cannot_use_preexisting_image(tmp_path):
    result, events = run_build(tmp_path, build_exit=23)
    assert result.returncode != 0, "preexisting image masked failed current build"
    assert result.stderr == ""
    assert "build-end" in events
    assert [p.read_text() for p in tmp_path.glob("build-run-*/exit-code")] == ["23\n"]
    assert "rollout" not in events


def test_success_waits_for_current_build_before_accepting_image(tmp_path):
    result, events = run_build(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "build-end" in events, events
    assert events.index("build-end") < events.index("inspect"), events
    assert events[-1] == "rollout"


def test_missing_current_build_result_cannot_use_preexisting_image(tmp_path):
    result, events = run_build(tmp_path, launch_lost=True)
    assert result.returncode != 0, "missing launch result was replaced by image presence"
    assert result.stderr == ""
    assert "rollout" not in events


def test_successful_build_requires_expected_image(tmp_path):
    result, events = run_build(tmp_path, image_present=False)
    assert result.returncode != 0
    assert result.stderr == ""
    assert [p.read_text() for p in tmp_path.glob("build-run-*/exit-code")] == ["0\n"]
    assert "rollout" not in events


def test_repeat_same_sha_cannot_reuse_previous_success_receipt(tmp_path):
    first, _ = run_build(tmp_path)
    assert first.returncode == 0, first.stderr
    second, events = run_build(tmp_path, build_exit=23)
    assert second.returncode != 0, "previous successful launch authorized failed rebuild"
    assert "rollout" not in events


def test_release_script_has_valid_bash_syntax():
    result = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
