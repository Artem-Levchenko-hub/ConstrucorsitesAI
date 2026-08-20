#!/usr/bin/env bash
set -euo pipefail

release_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
updater="${release_dir}/update-env-value.sh"
gate="${release_dir}/local-release-gate.sh"
rollback_manifest="${release_dir}/rollback-manifest.sh"
runbook="${release_dir}/README.md"
test_root="$(mktemp -d)"
trap 'rm -rf "${test_root}"' EXIT

fail() {
  echo "release tool test failed: $1" >&2
  exit 1
}

file_mode() {
  if stat -f '%Lp' "$1" >/dev/null 2>&1; then
    stat -f '%Lp' "$1"
  else
    stat -c '%a' "$1"
  fi
}

env_file="${test_root}/production.env"
printf 'A=1\nTARGET=old\nB=2\nTARGET=duplicate\n' >"${env_file}"
chmod 600 "${env_file}"
bash "${updater}" "${env_file}" TARGET new >"${test_root}/updater.out"
expected_file="${test_root}/expected.env"
printf 'A=1\nB=2\nTARGET=new\n' >"${expected_file}"
cmp -s "${env_file}" "${expected_file}" || fail "updater changed unrelated lines"
[[ "$(file_mode "${env_file}")" == "600" ]] || fail "updater changed file mode"
grep -Fxq "updated TARGET in ${env_file}" "${test_root}/updater.out" \
  || fail "updater did not print its redacted confirmation"

if bash "${updater}" "${env_file}" TARGET $'unsafe\nvalue' >/dev/null 2>&1; then
  fail "updater accepted a newline-containing value"
fi
if bash "${updater}" "${env_file}" 'unsafe-key' value >/dev/null 2>&1; then
  fail "updater accepted an unsafe key"
fi

rollback_record="${test_root}/release-record"
full_env_backup="${test_root}/full.env.pre-release"
orchestrator_env_backup="${test_root}/orchestrator.env.pre-release"
rollback_pointer="${test_root}/pending-rollback.json"
checked_out_release="${test_root}/checkout/infra/release"
checked_out_manifest="${checked_out_release}/rollback-manifest.sh"
checked_out_updater="${checked_out_release}/update-env-value.sh"
runtime_manifest="${test_root}/omnia-runtime/releases/rollback-manifest.sh"
runtime_updater="${test_root}/omnia-runtime/releases/update-env-value.sh"
rollback_candidate="${test_root}/rollback-candidate.env"
mkdir -p "${rollback_record}"
touch "${full_env_backup}" "${orchestrator_env_backup}"
mkdir -p "$(dirname "${checked_out_manifest}")" "$(dirname "${runtime_manifest}")"
cp "${rollback_manifest}" "${checked_out_manifest}"
cp "${updater}" "${checked_out_updater}"
install -m 700 "${checked_out_manifest}" "${runtime_manifest}"
install -m 700 "${checked_out_updater}" "${runtime_updater}"
rm -rf "${test_root}/checkout"
[[ ! -e "${test_root}/checkout" ]] \
  || fail "simulated rollback checkout retained release tooling"
grep -Fq \
  'rollback_manifest_tool=/opt/omnia-runtime/releases/rollback-manifest.sh' \
  "${runbook}" \
  || fail "runbook does not load the rollback helper outside the checkout"
grep -Fq \
  'install -m 700 infra/release/rollback-manifest.sh "${rollback_manifest_tool}"' \
  "${runbook}" \
  || fail "runbook does not persist the rollback helper before mutation"
grep -Fq \
  'rollback_env_updater=/opt/omnia-runtime/releases/update-env-value.sh' \
  "${runbook}" \
  || fail "runbook does not load the rollback updater outside the checkout"
grep -Fq \
  'install -m 700 infra/release/update-env-value.sh "${rollback_env_updater}"' \
  "${runbook}" \
  || fail "runbook does not persist the rollback updater before mutation"
release_sha=0123456789abcdef0123456789abcdef01234567
rollback_sha=89abcdef0123456789abcdef0123456789abcdef
printf 'OMNIA_RELEASE_SHA=old\n' >"${rollback_candidate}"
bash "${runtime_manifest}" write \
  "${rollback_pointer}" \
  "${rollback_record}" \
  "${full_env_backup}" \
  "${orchestrator_env_backup}" \
  "${release_sha}" \
  "${rollback_sha}" >/dev/null
[[ "$(file_mode "${rollback_pointer}")" == "600" ]] \
  || fail "rollback pointer is not permission protected"
if ! env -i PATH="${PATH}" bash -c '
  set -euo pipefail
  tool="$1"
  pointer="$2"
  expected_record="$3"
  expected_full="$4"
  expected_orchestrator="$5"
  expected_release="$6"
  expected_rollback="$7"
  updater="$8"
  candidate="$9"
  test "$(bash "${tool}" read "${pointer}" release_record)" = "${expected_record}"
  test "$(bash "${tool}" read "${pointer}" full_env_backup)" = "${expected_full}"
  test "$(bash "${tool}" read "${pointer}" orchestrator_env_backup)" = "${expected_orchestrator}"
  test "$(bash "${tool}" read "${pointer}" release_sha)" = "${expected_release}"
  test "$(bash "${tool}" read "${pointer}" rollback_sha)" = "${expected_rollback}"
  bash "${updater}" "${candidate}" OMNIA_RELEASE_SHA "${expected_rollback}" >/dev/null
  grep -Fxq "OMNIA_RELEASE_SHA=${expected_rollback}" "${candidate}"
' _ \
  "${runtime_manifest}" \
  "${rollback_pointer}" \
  "${rollback_record}" \
  "${full_env_backup}" \
  "${orchestrator_env_backup}" \
  "${release_sha}" \
  "${rollback_sha}" \
  "${runtime_updater}" \
  "${rollback_candidate}"; then
  fail "rollback bundle was not recoverable from a clean shell after checkout"
fi

repo="${test_root}/repo"
mkdir -p \
  "${repo}/infra/release" \
  "${repo}/apps/api" \
  "${repo}/apps/orchestrator" \
  "${repo}/apps/web" \
  "${repo}/apps/llm-gateway/deploy/full" \
  "${test_root}/bin"
cp "${gate}" "${repo}/infra/release/local-release-gate.sh"
cat >"${repo}/tracked.txt" <<'EOF'
release gate fixture
EOF
touch \
  "${repo}/apps/api/.keep" \
  "${repo}/apps/orchestrator/.keep" \
  "${repo}/apps/web/.keep" \
  "${repo}/apps/llm-gateway/deploy/full/docker-compose.yml"
git -C "${repo}" init -q
git -C "${repo}" config user.email release-test@example.com
git -C "${repo}" config user.name "Release Test"
git -C "${repo}" add .
git -C "${repo}" commit -qm fixture
head_sha="$(git -C "${repo}" rev-parse HEAD)"
sentinel="${test_root}/build-command-ran"
secret_value="release-test-password-do-not-print"
docker_calls="${test_root}/docker-calls.log"

cat >"${test_root}/bin/uv" <<EOF
#!/usr/bin/env bash
touch "${sentinel}"
exit 91
EOF
cat >"${test_root}/bin/docker" <<EOF
#!/usr/bin/env bash
touch "${sentinel}"
exit 91
EOF
cat >"${test_root}/bin/corepack" <<EOF
#!/usr/bin/env bash
touch "${sentinel}"
exit 91
EOF
chmod +x "${test_root}/bin/uv" "${test_root}/bin/docker" "${test_root}/bin/corepack"

run_gate_expect_failure() {
  local expected_sha="$1"
  local output="$2"
  if (
    cd "${repo}"
    EXPECTED_RELEASE_SHA="${expected_sha}" \
      DATABASE_URL="postgresql://user:${secret_value}@db/prod" \
      DATABASE_TEST_URL="postgresql://user:${secret_value}@db/test" \
      JWT_SECRET="${secret_value}" \
      PATH="${test_root}/bin:${PATH}" \
      bash infra/release/local-release-gate.sh
  ) >"${output}" 2>&1; then
    fail "release gate unexpectedly passed preflight"
  fi
  [[ ! -e "${sentinel}" ]] || fail "release gate ran a build command before preflight"
  if grep -Fq "${secret_value}" "${output}"; then
    fail "release gate printed an environment value"
  fi
}

run_gate_expect_failure aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "${test_root}/mismatch.out"
grep -Eiq 'revision|release|sha|match' "${test_root}/mismatch.out" \
  || fail "release mismatch was not explained"

run_gate_expect_failure unknown "${test_root}/unknown.out"
grep -Eiq 'unknown|invalid' "${test_root}/unknown.out" \
  || fail "unknown release was not rejected explicitly"

run_gate_expect_failure "${head_sha}" "${test_root}/remote-database.out"
grep -Eiq 'loopback|local' "${test_root}/remote-database.out" \
  || fail "a remote database target was not rejected explicitly"

for command_name in uv docker corepack; do
  cat >"${test_root}/bin/${command_name}" <<'EOF'
#!/usr/bin/env bash
printf 'database=%s jwt=%s\n' "${DATABASE_URL:-}" "${JWT_SECRET:-}"
exit 0
EOF
  chmod +x "${test_root}/bin/${command_name}"
done
cat >"${test_root}/bin/docker" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == compose ]]; then
  for required_name in \
    OMNIA_RELEASE_SHA \
    API_IMAGE \
    WEB_IMAGE \
    JWT_SECRET \
    SECRETS_ENCRYPTION_KEY \
    ORCHESTRATOR_INTERNAL_TOKEN \
    NEXTAUTH_SECRET; do
    [[ -n "${!required_name:-}" ]] || exit 92
  done
fi
printf '%s\n' "$*" >>"${RELEASE_GATE_TEST_DOCKER_CALLS}"
if [[ "$1" == inspect && "$2" == --format ]]; then
  echo healthy
  exit 0
fi
printf 'database=%s jwt=%s\n' "${DATABASE_URL:-}" "${JWT_SECRET:-}"
exit 0
EOF
chmod +x "${test_root}/bin/docker"
if ! (
  cd "${repo}"
  EXPECTED_RELEASE_SHA="${head_sha}" \
    DATABASE_URL="postgresql+asyncpg://user:${secret_value}@127.0.0.1/release_gate" \
    DATABASE_TEST_URL="postgresql+asyncpg://user:${secret_value}@127.0.0.1/release_gate_test" \
    JWT_SECRET="${secret_value}" \
    RELEASE_GATE_TEST_DOCKER_CALLS="${docker_calls}" \
    PATH="${test_root}/bin:${PATH}" \
    bash infra/release/local-release-gate.sh
) >"${test_root}/gate-success.out" 2>&1; then
  sed -n '1,120p' "${test_root}/gate-success.out" >&2
  fail "release gate did not complete with clean local inputs and successful commands"
fi
evidence_dir="$(find "${repo}/.release-evidence" -mindepth 1 -maxdepth 1 -type d -print -quit)"
[[ -n "${evidence_dir}" ]] || fail "release gate did not create evidence"
for evidence_file in manifest.json api.log orchestrator.log web.log compose.log images.log; do
  [[ -f "${evidence_dir}/${evidence_file}" ]] || fail "release gate omitted ${evidence_file}"
done
grep -Fq "build api web" "${docker_calls}" \
  || fail "release gate did not build the production services"
grep -Fq "omnia-api:${head_sha}" "${docker_calls}" \
  || fail "release gate did not smoke the revision-tagged API image"
grep -Fq "omnia-web:${head_sha}" "${docker_calls}" \
  || fail "release gate did not smoke the revision-tagged web image"
grep -Fq "/app/.venv/bin/alembic upgrade head" "${docker_calls}" \
  || fail "release gate did not migrate an isolated container database"
grep -Fq "/app/.venv/bin/uvicorn" "${docker_calls}" \
  || fail "release gate did not start the API container"
grep -Fq "node -e" "${docker_calls}" \
  || fail "release gate did not verify the web container health endpoint"
python3 - "${evidence_dir}/manifest.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as manifest_file:
    manifest = json.load(manifest_file)
assert manifest["status"] == "passed"
assert manifest["steps"]
assert all(set(step) == {"name", "command", "started_at", "finished_at", "exit_code"} for step in manifest["steps"])
assert all(step["command"] not in {"bash", "env", "local_container_smoke"} for step in manifest["steps"])
PY
if grep -R -Fq "${secret_value}" "${evidence_dir}"; then
  fail "release evidence contains an unredacted environment value"
fi
grep -R -Fq '[REDACTED]' "${evidence_dir}" \
  || fail "release evidence did not retain a redacted command-output marker"
if find "${repo}/.release-evidence" -path "*${secret_value}*" -print -quit | grep -q .; then
  fail "an evidence path contains an environment value"
fi
rm -rf "${repo}/.release-evidence"

printf 'dirty\n' >"${repo}/dirty.txt"
run_gate_expect_failure "${head_sha}" "${test_root}/dirty.out"
grep -Eiq 'clean|dirty|uncommitted' "${test_root}/dirty.out" \
  || fail "dirty tree was not rejected explicitly"

if find "${repo}" -path "*${secret_value}*" -print -quit | grep -q .; then
  fail "an evidence path contains an environment value"
fi

echo "release tool contract tests passed"
