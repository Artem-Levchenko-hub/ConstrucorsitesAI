"""Contracts for one production release over a historical, never-sent request."""

import importlib.util
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, skipUnless
from unittest.mock import patch
from uuid import UUID

HERE = Path(__file__).resolve().parent
TARGET = "a" * 40
INCUMBENT = "b" * 40
RUN, ATTEMPT, USER, PROJECT = (str(UUID(int=i)) for i in range(1, 5))


def load(filename):
    spec = importlib.util.spec_from_file_location(filename, HERE / (filename + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scope():
    core = {
        "version": 1, "release_sha": TARGET, "reader_release_sha": INCUMBENT,
        "state_revision": 79, "source_sha256": "0" * 64,
    }
    return {
        "gateway_runtime": {"container_id": "f" * 64, "image_id": "sha256:" + "a" * 64},
        "api": {
            **core, "run_id": RUN, "attempt_id": ATTEMPT,
            "context_sha256": "1" * 64, "clock_sha256": "2" * 64,
        },
        "gateway": {
            **core, "action": "close_expired_before_dispatch",
            "identity": {
                "version": 1, "run_id": RUN, "attempt_id": ATTEMPT,
                "user_id": USER, "project_id": PROJECT,
                "prompt_hash": "3" * 64, "request_sha256": "4" * 64,
            },
        },
    }


class ScopeTests(TestCase):
    def setUp(self):
        self.launcher = load("generation-expired-provider-images")

    def test_digest_covers_both_exact_envelopes_and_is_canonical(self):
        request = scope()
        run, incumbent, original = self.launcher.scope_description(request, TARGET)
        self.assertEqual((run, incumbent), (RUN, INCUMBENT))
        reversed_request = dict(reversed(request.items()))
        self.assertEqual(self.launcher.scope_description(reversed_request, TARGET)[2], original)
        request["gateway"]["identity"]["request_sha256"] = "f" * 64
        self.assertNotEqual(self.launcher.scope_description(request, TARGET)[2], original)

    def test_reader_and_writer_cannot_select_different_rows_epochs_or_revisions(self):
        for field, value in (("attempt_id", USER), ("run_id", PROJECT), ("state_revision", True),
                             ("release_sha", INCUMBENT), ("reader_release_sha", TARGET),
                             ("source_sha256", "f" * 64), ("unknown", None)):
            with self.subTest(field=field):
                request = scope()
                request["api"][field] = value
                with self.assertRaises(self.launcher.ReleaseDenied):
                    self.launcher.scope_description(request, TARGET)
        with self.assertRaises(self.launcher.ReleaseDenied):
            self.launcher.decoded(b'{"api":null,"api":null}')

    def test_released_repair_requires_same_context_in_both_envelopes(self):
        request = scope()
        request["gateway"]["action"] = "close_released_before_dispatch"
        with self.assertRaises(self.launcher.ReleaseDenied):
            self.launcher.scope_description(request, TARGET)
        request["gateway"]["context_sha256"] = request["api"]["context_sha256"]
        self.assertEqual(self.launcher.scope_description(request, TARGET)[:2], (RUN, INCUMBENT))
        request["gateway"]["context_sha256"] = "a" * 64
        with self.assertRaises(self.launcher.ReleaseDenied):
            self.launcher.scope_description(request, TARGET)

    def test_wrong_image_labels_or_docker_failure_cannot_reuse_an_image(self):
        names = self.launcher.image_names(TARGET)
        rows = [
            {
                "RepoTags": [name],
                "Config": {"Labels": {
                    self.launcher.IMAGE_REVISION: TARGET, self.launcher.IMAGE_ROLE: name_role,
                }},
            }
            for name_role, name in names.items()
        ]
        with patch.object(self.launcher.subprocess, "run") as run:
            run.return_value = SimpleNamespace(returncode=1)
            self.assertFalse(self.launcher.images_ready(names, TARGET))
            run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(rows))
            self.assertTrue(self.launcher.images_ready(names, TARGET))
            rows[1]["Config"]["Labels"][self.launcher.IMAGE_ROLE] = "api"
            run.return_value.stdout = json.dumps(rows)
            self.assertFalse(self.launcher.images_ready(names, TARGET))

    def test_one_off_disables_build_and_pull_on_current_production_compose(self):
        with patch.object(self.launcher, "command", return_value=b'{"ok":true}') as command:
            result = self.launcher.invoke(
                ["docker", "compose"], {}, role="gateway", module="approved", scope={},
            )
        argv = command.call_args.args[0]
        self.assertEqual(argv[:9], ["docker", "compose", "run", "--build=false", "--no-deps",
                                    "--pull", "never", "-T", "--rm"])
        self.assertEqual(result, {"ok": True})

    def test_migration_one_off_uses_only_the_staged_image_and_cannot_dispatch(self):
        request = scope()
        response = {
            "ok": True, "head": self.launcher.HEAD, "action": "upgraded",
            "settled": False, "release_sha": TARGET, "reader_release_sha": INCUMBENT,
        }
        image_id = "sha256:" + "a" * 64
        images = self.launcher.image_names(TARGET)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(self.launcher, "command", return_value=image_id.encode()) as command,
                patch.object(self.launcher, "require_database") as database,
                patch.object(self.launcher, "invoke", return_value=response) as invoke,
            ):
                result = self.launcher.run_migration(root, [], {}, images, "opaque", request["api"], TARGET, INCUMBENT)
                self.assertEqual(result, {"ok": True, "mode": "migrate", "settled": False})
                alias = "yleum-expired-provider-migration:" + TARGET
                self.assertEqual(json.loads((root / "release-migration.json").read_text())["services"]["api"]["image"], alias)
                self.assertEqual(database.call_args.args[2], {**images, "api": alias})
                self.assertEqual(command.call_args_list[1].args[0], ["docker", "tag", image_id, alias])
                self.assertEqual(invoke.call_count, 1)
                self.assertEqual(invoke.call_args.kwargs["role"], "api")
                self.assertEqual(invoke.call_args.kwargs["scope"], request["api"])
                invoke.return_value = {**response, "release_sha": INCUMBENT}
                with self.assertRaises(self.launcher.ReleaseDenied):
                    self.launcher.run_migration(root, [], {}, images, "opaque", request["api"], TARGET, INCUMBENT)

    def test_changed_incumbent_and_duplicate_gateway_deny_writer_authority(self):
        gateway = scope()["gateway_runtime"]
        container, image = gateway["container_id"], gateway["image_id"]
        row = {"Id": container, "Image": image, "Config": {"Image": "omnia-gateway:prod"}}
        with patch.object(self.launcher, "command", side_effect=[image.encode(), container.encode()]):
            self.launcher.require_gateway_writer(row, gateway)
        with patch.object(self.launcher, "command", return_value=b"changed"):
            with self.assertRaises(self.launcher.ReleaseDenied):
                self.launcher.require_gateway_writer(row, gateway)
        duplicate = "\t".join(json.dumps(value) for value in ("/old", image, "worker:old", "other"))
        with patch.object(self.launcher, "command", side_effect=[
            image.encode(), (container + "\n" + "x" * 64).encode(), duplicate.encode(),
        ]) as command:
            with self.assertRaises(self.launcher.ReleaseDenied):
                self.launcher.require_gateway_writer(row, gateway)
            self.assertEqual(command.call_count, 3)


@skipUnless(shutil.which("bash"), "bash required")
class ScopedLaunchTests(TestCase):
    def release_function(self, name):
        source = (HERE / "deploy-prod.sh").read_text(encoding="utf-8")
        header = name + "() {"
        body = source.split(header, 1)[1].split("\n}\n", 1)[0]
        return header + body + "\n}\n"

    def test_scoped_preapply_remains_retriable_across_lost_seal_ack(self):
        program = r"""
set -euo pipefail
SCENARIO="$1"
record() { printf '%s\n' "$1" >> calls; }
publication_drain() { record "publication-$1"; [ "$SCENARIO" != "publication" ]; }
expired_provider_check() {
  record "$1"
  if [ "$1" = settled ]; then [ -f sealed ]; else [ ! -f sealed ]; fi
}
expired_provider_images() {
  record "$1"
  if [ "$1" = seal ]; then
    touch sealed
    [ "$SCENARIO" != "lost-ack" ]
  fi
}
say() { :; }
""" + self.release_function("expired_provider_preapply") + "expired_provider_preapply\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = root / "calls"
            script = root / "launch.sh"
            script.write_text(program, encoding="utf-8", newline="\n")
            for scenario, result_expected, steps in (
                ("publication", False, ["publication-core"]),
                ("lost-ack", False, ["publication-core", "publication-commerce", "settled", "eligibility",
                                     "migrate", "eligibility", "publication-core", "publication-commerce", "seal"]),
                ("retry", True, ["publication-core", "publication-commerce", "settled"]),
            ):
                with self.subTest(scenario=scenario):
                    calls.unlink(missing_ok=True)
                    result = subprocess.run(
                        [shutil.which("bash"), script.name, scenario],
                        cwd=root, capture_output=True, timeout=15,
                    )
                    self.assertEqual(result.returncode == 0, result_expected, result.stderr)
                    self.assertEqual(calls.read_text().splitlines(), steps)

    def test_finalization_resumes_after_lost_sql_ack_without_reopening_barriers(self):
        import shlex
        import sys

        python = shlex.quote(sys.executable.replace("\\", "/"))
        program = f'python3() {{ {python} "$@"; }}\n' + r"""
set -euo pipefail
SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
API_RUNTIME_SHA="$SHA"
READER_NONCE=11111111-1111-4111-8111-111111111111
SCENARIO="$1"
case "$SCENARIO" in hold-attach|attach) EXPIRED_REATTACH=1;; esac
record() { printf '%s\n' "$1" >> calls; }
api_maintenance() {
  [ "$3" = status ]
  record sql-status
  if [ -f released ]; then
    printf '{"release_sha":null}'
  elif [ "$SCENARIO" = foreign ]; then
    printf '{"release_sha":"foreign","bootstrap_readonly":false}'
  else
    printf '{"release_sha":"%s","bootstrap_readonly":false}' "$SHA"
  fi
}
reader_control() {
  record "$1-$2"
  if [ "$2" != verify-current ]; then [ "$5" = --owner-nonce ] && [ "$6" = "$READER_NONCE" ]; fi
  if [ "$1-$2" = "commerce-clear" ]; then [ "$SCENARIO" != lost-clear ]; fi
}
publication_drain() { record "publication-$1"; }
expired_provider_gateway_cutover() { record gateway; [ "$SCENARIO" != unhealthy ]; }
expired_provider_images() { record "$1"; [ "$SCENARIO" != hold-attach ]; }
verify_billing_runtime() { record billing; }
expired_provider_end() {
  record end-sql
  touch released
  [ "$SCENARIO" != lost-sql-ack ]
}
ingress_drain() { [ "$1" = clear-parked-source ]; record "$1"; }
""" + self.release_function("expired_provider_finish_open_ingress") + "expired_provider_finish_open_ingress\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "finalize.sh"
            script.write_text(program, encoding="utf-8", newline="\n")
            calls = root / "calls"
            for scenario, succeeds, steps in (
                ("foreign", False, ["sql-status"]),
                ("unhealthy", False, ["sql-status", "core-verify-current", "core-verify-owner",
                                      "commerce-verify-owner", "publication-core", "publication-commerce", "gateway"]),
                ("lost-sql-ack", False, ["sql-status", "core-verify-current", "core-verify-owner",
                                        "commerce-verify-owner", "publication-core", "publication-commerce",
                                        "gateway", "billing", "end-sql"]),
                ("lost-clear", False, ["sql-status", "core-clear", "commerce-clear"]),
                ("final-retry", True, ["sql-status", "core-clear", "commerce-clear",
                                       "core-verify-current", "clear-parked-source"]),
                ("hold-attach", False, ["sql-status", "core-clear", "commerce-clear",
                                        "core-verify-current", "authorize-reattach"]),
                ("attach", True, ["sql-status", "core-clear", "commerce-clear",
                                  "core-verify-current", "authorize-reattach", "clear-parked-source"]),
            ):
                with self.subTest(scenario=scenario):
                    calls.unlink(missing_ok=True)
                    result = subprocess.run(
                        [shutil.which("bash"), script.name, scenario], cwd=root, capture_output=True, timeout=15,
                    )
                    self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                    self.assertEqual(calls.read_text().splitlines(), steps)
