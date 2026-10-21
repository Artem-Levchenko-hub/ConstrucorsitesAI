"""Owned release finalization: real shell gates with inert transport boundaries."""

import copy
import hashlib
import io
import json
import shlex
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_generation_reader_deploy import BASH, PYTHON_BRIDGE

REPOSITORY = "registry.yleum.ru/platform/omnia-api"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
OCI_LAYER = "application/vnd.oci.image.layer.v1.tar+gzip"


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def billing_fixture(*, legacy=False, fault=""):
    blobs = {}

    def blob(document, media_type):
        raw = encoded(document)
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        blobs[digest] = raw.decode()
        return {"mediaType": media_type, "digest": digest, "size": len(raw)}

    config = {"os": "windows" if fault == "config_platform" else "linux", "architecture": "amd64",
              "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "a" * 64]}}
    config_descriptor = blob(config, OCI_CONFIG)
    layer = {"mediaType": OCI_LAYER, "digest": "sha256:" + "b" * 64, "size": 123}
    leaf = {"schemaVersion": 2, "mediaType": OCI_MANIFEST,
            "config": config_descriptor, "layers": [layer]}
    if fault == "foreign_layer":
        layer["urls"] = ["https://untrusted.invalid/layer"]
    elif fault == "artifact":
        leaf["artifactType"] = "application/example"
    elif fault == "layer_encoding":
        layer["mediaType"] = "application/vnd.oci.image.layer.v1.tar+zstd"
    leaf_descriptor = blob(leaf, OCI_MANIFEST)

    def index(attestation):
        candidate = {**leaf_descriptor, "platform": {"os": "linux", "architecture": "amd64"}}
        if fault == "descriptor_size":
            candidate["size"] += 1
        elif fault == "descriptor_digest":
            candidate["digest"] = "sha256:malformed"
        elif fault == "attestation_only":
            candidate["annotations"] = {"vnd.docker.reference.type": "attestation-manifest"}
        manifests = [candidate, {"mediaType": OCI_MANIFEST, "digest": "sha256:" + attestation * 64,
                                "size": 100, "platform": {"os": "unknown", "architecture": "unknown"},
                                "annotations": {"vnd.docker.reference.type": "attestation-manifest"}}]
        if fault == "ambiguous_platform":
            manifests.append(copy.deepcopy(candidate))
        elif fault == "missing_platform":
            manifests[0]["platform"]["architecture"] = "arm64"
        return blob({"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": manifests}, OCI_INDEX)

    pinned = leaf_descriptor if legacy else index("c")
    actual = leaf_descriptor if legacy else index("d")
    if fault == "different_leaf":
        other_leaf = copy.deepcopy(leaf)
        other_leaf["annotations"] = {"example": "distinct-leaf"}
        other = blob(other_leaf, OCI_MANIFEST)
        actual = blob({"schemaVersion": 2, "mediaType": OCI_INDEX,
                       "manifests": [{**other, "platform": {"os": "linux", "architecture": "amd64"}}]}, OCI_INDEX)
    image, actual_ref = REPOSITORY + "@" + pinned["digest"], REPOSITORY + "@" + actual["digest"]
    producer, container_id = "202610211100", "e" * 64
    template = {"metadata": {"annotations": {"omnia.release_sha": producer}},
                "spec": {"containers": [{"name": "worker", "image": image}]}}
    deployment = {"metadata": {"generation": 3}, "spec": {"replicas": 1, "template": copy.deepcopy(template)},
                  "status": {"observedGeneration": 3, "replicas": 1, "updatedReplicas": 1,
                             "readyReplicas": 1, "availableReplicas": 1}}
    pod = copy.deepcopy(template)
    pod["metadata"].update(name="billing-worker-fixture", namespace="billing", uid="11111111-1111-4111-8111-111111111111")
    pod["status"] = {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
                     "containerStatuses": [{"name": "worker", "ready": True, "state": {"running": {}},
                                            "containerID": "containerd://" + container_id,
                                            "image": config_descriptor["digest"], "imageID": actual_ref}]}
    image_status = {"status": {"id": config_descriptor["digest"], "repoDigests": list(dict.fromkeys([image, actual_ref]))},
                    "info": {"imageSpec": config}}
    live = {"status": {"id": container_id, "state": "CONTAINER_RUNNING", "metadata": {"name": "worker"},
                       "labels": {"io.kubernetes.container.name": "worker", "io.kubernetes.pod.name": pod["metadata"]["name"],
                                  "io.kubernetes.pod.namespace": "billing", "io.kubernetes.pod.uid": pod["metadata"]["uid"]},
                       "image": {"image": config_descriptor["digest"], "userSpecifiedImage": image},
                       "imageRef": actual_ref}}
    return {"image": image, "actual_ref": actual_ref, "producer": producer, "config_id": config_descriptor["digest"],
            "leaf_digest": leaf_descriptor["digest"], "deployment": deployment, "pods": {"items": [pod]},
            "images": {image: image_status, actual_ref: copy.deepcopy(image_status)}, "live": live, "blobs": blobs}


def runtime_source():
    source = Path(__file__).with_name("deploy-prod.sh").read_text(encoding="utf-8")
    return source.split("<<'BILLING_RUNTIME'\n", 1)[1].split("\nBILLING_RUNTIME", 1)[0]


def run_runtime(source, fixture, *, expected="a" * 40, image=None, producer=None):
    def command(arguments, **kwargs):
        if arguments == ["k3s", "kubectl", "-n", "billing", "get", "deployment", "billing-worker", "-o", "json"]:
            value = fixture["deployment"]
        elif arguments == ["k3s", "kubectl", "-n", "billing", "get", "pods", "-l", "app=billing-worker", "-o", "json"]:
            value = fixture["pods"]
        elif arguments[:3] == ["k3s", "crictl", "inspecti"] and len(arguments) == 4:
            value = fixture["images"][arguments[3]]
        elif arguments == ["k3s", "crictl", "inspect", "e" * 64]:
            value = fixture["live"]
        elif arguments[:6] == ["k3s", "ctr", "-n", "k8s.io", "content", "get"] and len(arguments) == 7:
            raw = fixture["blobs"][arguments[6]].encode()
            return SimpleNamespace(stdout=raw.decode() if kwargs.get("text") else raw)
        else:
            raise AssertionError("unexpected or mutating command: " + repr(arguments))
        raw = json.dumps(value)
        return SimpleNamespace(stdout=raw if kwargs.get("text") else raw.encode())

    with patch("sys.argv", ["billing-runtime", expected, image or fixture["image"], producer or fixture["producer"]]), \
            patch("subprocess.run", side_effect=command):
        exec(compile(source, "billing-runtime", "exec"), {})



@unittest.skipUnless(BASH, "bash required")
class OwnedReleaseFinalizationTests(unittest.TestCase):
    target = "a" * 40
    control = "b" * 40
    nonce = "11111111-1111-4111-8111-111111111111"

    def source(self):
        return Path(__file__).with_name("deploy-prod.sh").read_text()

    def function(self, name):
        marker = name + "() {"
        self.assertTrue(marker in self.source(), "owned finalization gate is absent: " + name)
        return marker + self.source().split(marker, 1)[1].split("\n}\n", 1)[0] + "\n}\n"

    def shell(self, script, *arguments, cwd=None):
        # A file preserves long nested heredocs across Windows' command-line boundary.
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary, "test-release.sh")
            source.write_text(PYTHON_BRIDGE + script, encoding="utf-8", newline="\n")
            return subprocess.run(
                [BASH, source.as_posix(), *arguments],
                cwd=cwd, capture_output=True, text=True, timeout=15,
            )

    def test_explicit_control_and_existing_owner_parse_without_starting_rollout(self):
        prefix = self.source().split('REPO="', 1)[0]
        result = self.shell(prefix + '\nprintf "parsed:%s:%s" "$FINALIZE_NONCE" "$CONTROL_SHA"\n',
                            self.target, "--no-web", "--finalize-owned-release", self.nonce,
                            "--control-revision", self.control)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "parsed:" + self.nonce + ":" + self.control)

    def test_finalization_rejects_unpaired_or_mutating_arguments(self):
        prefix = self.source().split('REPO="', 1)[0] + '\nprintf should-not-run\n'
        base = [self.target, "--no-web", "--finalize-owned-release", self.nonce,
                "--control-revision", self.control]
        cases = [
            [self.target, "--no-web", "--control-revision", self.control],
            [self.target, "--no-web", "--finalize-owned-release", self.nonce],
            [self.target, "--finalize-owned-release", self.nonce, "--control-revision", self.control],
            [*base, "--web-only"], [*base, "--gateway"], [*base, "--bootstrap-admission"],
            [*base, "--legal-version", "2026-09-25"], [*base, "--repair-window", "3600"],
            [*base[:3], "invalid-nonce", *base[4:]],
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.shell(prefix, *arguments)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn("should-not-run", result.stdout)

    def test_preflight_requires_existing_barriers_before_shared_ending(self):
        functions = self.function("finalize_preflight")
        for failure in ("", "ingress", "owner-commerce", "sql", "target", "expected", "billing"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                script = '''set -euo pipefail
SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
API_RUNTIME_SHA=$SHA
FINALIZE_NONCE=11111111-1111-4111-8111-111111111111
record() { printf '%s\\n' "$1" >> calls; [ "$1" != "$FAILURE" ]; }
ingress_drain() { [ "$1" = check ]; record ingress; }
reader_control() { [ "$2" = verify-owner ]; record "owner-$1"; }
reader_resume_check() { record sql; }
reader_target() { record target; }
verify_expected_revisions() { record expected; }
verify_billing_runtime() { record billing; }
''' + functions + '\nfinalize_preflight\nprintf ended >> calls\n'
                result = self.shell('FAILURE=' + shlex.quote(failure) + '\n' + script, cwd=temp)
                calls = Path(temp, "calls").read_text()
                if failure:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("ended", calls)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(calls.splitlines(), ["ingress", "owner-core", "owner-commerce",
                                                         "sql", "target", "expected", "billing", "ended"])
        result = self.shell('set -euo pipefail\nSHA=' + self.target + '\nAPI_RUNTIME_SHA=' + self.control
                            + '\nFINALIZE_NONCE=' + self.nonce + '\n' + functions + '\nfinalize_preflight\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime", result.stderr)

    def test_control_source_guard_rejects_runtime_changes_and_non_green_ci(self):
        functions = self.function("control_preflight")
        for damage in (
            "", "inspector_contract", "gateway_contract", "gateway_neighbor", "source", "runtime",
            "renamed", "ancestry", "ci", "ci_sha",
        ):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                driver = root / "driver"
                driver.write_bytes(b"reviewed-driver\n")
                expected = root / "committed"
                expected.write_bytes(b"unreviewed-driver\n" if damage == "source" else driver.read_bytes())
                changed = "apps/api/src/unsafe.py" if damage == "runtime" else (
                    "infra/release/not-allowed.sh" if damage == "renamed" else "infra/release/generation-publication-drain.py")
                if damage == "inspector_contract":
                    changed = "apps/orchestrator/tests/test_inspector_upstream_contract.py"
                elif damage == "gateway_contract":
                    changed = "apps/api/tests/test_gateway_billing_cutover_release.py"
                elif damage == "gateway_neighbor":
                    changed = "apps/api/tests/test_gateway_billing_cutover_release_other.py"
                script = '''set -euo pipefail
SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
CONTROL_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
REPO=.
RELEASE_DRIVER=driver
git() {
 case " $* " in
  *" merge-base "*) [ "$DAMAGE" != ancestry ]; ;;
  *" show "*) cat committed ;;
  *" diff "*) printf '%s\\0' "$CHANGED" ;;
  *) return 99 ;;
 esac
}
gh() { cat ci.json; }
''' + functions + '\ncontrol_preflight\nprintf accepted\n'
                run = {"id": 123, "head_sha": self.target if damage == "ci_sha" else self.control,
                       "head_branch": "main", "event": "push", "status": "completed",
                       "conclusion": "failure" if damage == "ci" else "success",
                       "path": ".github/workflows/ci.yml", "run_attempt": 1,
                       "created_at": "2026-10-21T00:00:00Z"}
                (root / "ci.json").write_text(json.dumps({"workflow_runs": [run]}))
                result = self.shell('DAMAGE=' + shlex.quote(damage) + '\nCHANGED=' + shlex.quote(changed)
                                    + '\n' + script, cwd=temp)
                if damage not in {"", "inspector_contract", "gateway_contract"}:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("accepted", result.stdout)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("accepted", result.stdout)

    def test_billing_uses_target_image_content_even_with_legacy_producer_label(self):
        functions = self.function("verify_billing_runtime")
        fixture = billing_fixture(legacy=True)
        image, image_id = fixture["image"], fixture["config_id"]
        for damage in ("", "timestamp", "target_image", "digest", "annotation", "pod_digest", "not_ready"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                producer = "202610210831" if damage == "timestamp" else self.target[:12]
                (root / "binding").write_text("IMAGE_REF=" + image + "\nIMAGE_TAG=registry.yleum.ru/platform/omnia-api:"
                                            + producer + "\nRELEASE_SHA=" + producer + "\n")
                (root / "image").write_text(json.dumps({"id": image_id, "repo_digests": [] if damage == "digest" else [image]}))
                (root / "api").write_text("sha256:" + "e" * 64 if damage == "target_image" else image_id)
                template = {"metadata": {"annotations": {"omnia.release_sha": producer}},
                            "spec": {"containers": [{"name": "worker", "image": image}]}}
                deployment = {"metadata": {"generation": 3},
                              "spec": {"replicas": 1, "template": template},
                              "status": {"observedGeneration": 3, "replicas": 1, "updatedReplicas": 1,
                                         "readyReplicas": 1, "availableReplicas": 1}}
                pod = json.loads(json.dumps(template))
                pod["metadata"].update(fixture["pods"]["items"][0]["metadata"])
                pod["metadata"]["annotations"]["omnia.release_sha"] = producer
                pod["status"] = {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
                                 "containerStatuses": copy.deepcopy(fixture["pods"]["items"][0]["status"]["containerStatuses"])}
                pod["status"]["containerStatuses"][0]["ready"] = damage != "not_ready"
                if damage == "annotation":
                    pod["metadata"]["annotations"]["omnia.release_sha"] = self.control[:12]
                elif damage == "pod_digest":
                    pod["status"]["containerStatuses"][0]["imageID"] = "sha256:" + "f" * 64
                runtime = copy.deepcopy(fixture)
                runtime.update(deployment=deployment, pods={"items": [pod]})
                (root / "runtime.json").write_text(json.dumps(runtime))
                (root / "runtime_fixture.py").write_text("import json, sys\nfrom pathlib import Path\n"
                    + "sys.path.insert(0, " + repr(str(Path(__file__).resolve().parent)) + ")\n"
                    + "from test_owned_release_finalize_contract import run_runtime\n"
                    + "run_runtime(sys.stdin.read(), json.loads(Path('runtime.json').read_text()), "
                    + "image=sys.argv[1], producer=sys.argv[2])\n")
                script = '''set -euo pipefail
SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
ssh() {
 case "$2" in
  *billing-image.ref*) cat binding ;;
  *"docker image inspect"*) cat image ;;
  *"docker inspect yleum-prod-api"*) cat api ;;
  *) python3 runtime_fixture.py "$IMAGE" "$PRODUCER" ;;
 esac
}
''' + functions + '\nverify_billing_runtime\nprintf accepted\n'
                result = self.shell('IMAGE=' + shlex.quote(image) + '\nPRODUCER=' + shlex.quote(producer)
                                    + '\n' + script, cwd=temp)
                if damage in {"", "timestamp"}:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("accepted", result.stdout)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("accepted", result.stdout)

    def test_expected_revisions_are_read_without_rewriting_unchanged_web(self):
        functions = self.function("verify_expected_revisions")
        for damage in ("", "role", "environment", "web", "missing"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                repository = {"PRODUCTION_EXPECTED_" + role + "_RELEASE_SHA": self.target
                              for role in ("API", "WORKER", "GENERATION_WORKER", "ORCHESTRATOR")}
                repository["PRODUCTION_EXPECTED_RELEASE_SHA"] = self.target
                repository["PRODUCTION_EXPECTED_WEB_RELEASE_SHA"] = "c" * 40
                environment = {"PRODUCTION_EXPECTED_RELEASE_SHA": self.target}
                if damage == "role":
                    repository["PRODUCTION_EXPECTED_WORKER_RELEASE_SHA"] = self.control
                elif damage == "environment":
                    environment["PRODUCTION_EXPECTED_RELEASE_SHA"] = self.control
                elif damage == "missing":
                    del repository["PRODUCTION_EXPECTED_GENERATION_WORKER_RELEASE_SHA"]
                for name, values in (("repository", repository), ("environment", environment)):
                    (root / name).write_text(json.dumps([{"name": key, "value": value} for key, value in values.items()]))
                (root / "web").write_text(json.dumps({"status": "ok", "service": "web",
                                                     "release_sha": self.target if damage == "web" else "c" * 40}))
                script = '''set -euo pipefail
SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
REPO=.
gh() {
 case "$*" in
  "variable list --json name,value") cat repository ;;
  "variable list --env production --json name,value") cat environment ;;
  *) return 99 ;;
 esac
}
ssh() { cat web; }
''' + functions + '\nverify_expected_revisions\nprintf accepted\n'
                result = self.shell(script, cwd=temp)
                self.assertEqual(result.returncode == 0, not damage, result.stderr)
                self.assertEqual("accepted" in result.stdout, not damage)

    def test_shared_end_never_opens_on_bad_runtime_or_failed_physical_gate(self):
        functions = self.function("finish_owned_release")
        for failure in ("", "billing", "billing_short", "billing_revision", "controller_revision", "physical", "verify-owner-commerce", "gateway"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                dependencies = {role + "_release_sha": self.target
                                for role in ("worker", "generation_worker", "orchestrator", "billing_worker")}
                dependencies["billing_worker"] = "ok"
                if failure == "billing_revision":
                    dependencies["billing_worker_release_sha"] = self.control
                elif failure == "billing_short":
                    dependencies["billing_worker_release_sha"] = self.target[:12]
                health = {"status": "ok", "release_sha": self.target, "dependencies": dependencies}
                (root / "health").write_text(json.dumps(health))
                controller = {"status": "ok", "release_sha": self.control if failure == "controller_revision" else self.target}
                (root / "controller").write_text(json.dumps(controller))
                (root / "health_fixture.py").write_text('''import io, json, os, sys
from pathlib import Path
from unittest.mock import patch
os.environ["OMNIA_RELEASE_SHA"] = sys.argv[1]
os.environ["ORCHESTRATOR_HOSTS"] = json.dumps([{"name": "core", "url": "http://core"}, {"name": "commerce", "url": "http://commerce"}])
def request(url, **kwargs):
    return io.StringIO(Path("health" if url == "https://yleum.ru/api/health" else "controller").read_text())
with patch("urllib.request.urlopen", side_effect=request):
    exec(compile(sys.stdin.read(), "release-health", "exec"))
''')
                script = '''set -euo pipefail
SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
READER_NONCE=11111111-1111-4111-8111-111111111111
GW=0
record() { printf '%s\\n' "$1" >> calls; [ "$1" != "$FAILURE" ]; }
verify_billing_runtime() { record billing; }
api_maintenance() {
 if [ "$1" = - ]; then python3 health_fixture.py "$SHA"; else record sql-end; fi
}
ssh() { record gateway; }
reader_target() { record target; }
seq() { printf '1\\n'; }
ingress_drain() { record "ingress-$1"; }
quiescence_gate() { record physical; }
reader_control() { record "$2-$1"; }
''' + functions + '\nfinish_owned_release\nprintf accepted\n'
                result = self.shell('FAILURE=' + shlex.quote(failure) + '\n' + script, cwd=temp)
                calls = (root / "calls").read_text() if (root / "calls").exists() else ""
                if failure not in {"", "billing_short"}:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("ingress-end", calls)
                    self.assertNotIn("sql-end", calls)
                    self.assertNotIn("clear-", calls)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(calls.splitlines()[-4:], ["ingress-end", "sql-end", "clear-core", "clear-commerce"])

    def test_finalize_main_path_exits_before_rollout_and_holds_mutex_until_end(self):
        main = 'say "выкатка' + self.source().split('say "выкатка', 1)[1]
        with tempfile.TemporaryDirectory() as temp:
            script = '''set -euo pipefail
SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
FINALIZE_NONCE=11111111-1111-4111-8111-111111111111
CONTROL_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
SHORT=aaaaaaaa
REPO=.
API=1 WEB=0 GW=0 LEGAL= REPAIR=3600
say() { :; }
git() { case " $* " in *" fetch "*|*" merge-base "*) :;; *) return 99;; esac; }
control_preflight() { printf 'control\\n' >> calls; }
ssh() {
 case "$*" in
  *noclobber*) printf 'lock\\n' >> calls ;;
  *"rm -f /opt/omnia/.deploy.lock"*) printf 'unlock\\n' >> calls ;;
  *) return 99 ;;
 esac
}
api_maintenance() { [ "$1" = --describe-runtime ]; printf 'describe\\n' >> calls; printf '%s' "$SHA"; }
finalize_preflight() { printf 'preflight\\n' >> calls; }
finish_owned_release() { printf 'finish\\n' >> calls; }
''' + main
            result = self.shell(script, cwd=temp)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(temp, "calls").read_text().splitlines(),
                             ["control", "lock", "describe", "preflight", "finish", "unlock"])


class BillingRuntimeIdentityTests(unittest.TestCase):
    def accepted(self, fixture):
        output = io.StringIO()
        with redirect_stdout(output):
            run_runtime(runtime_source(), fixture)
        self.assertIn("single ready runtime confirmed", output.getvalue())

    def rejected(self, fixture):
        with self.assertRaises((AssertionError, KeyError, ValueError, TypeError)):
            run_runtime(runtime_source(), fixture)

    def test_two_exact_indexes_with_distinct_attestations_select_same_verified_leaf(self):
        self.accepted(billing_fixture())

    def test_legacy_leaf_pin_and_supported_runtime_reference_prefixes_remain_valid(self):
        self.accepted(billing_fixture(legacy=True))
        for prefix in ("docker-pullable://", "containerd://"):
            fixture = billing_fixture()
            fixture["pods"]["items"][0]["status"]["containerStatuses"][0]["imageID"] = (
                prefix + (fixture["actual_ref"].split("@", 1)[1] if prefix == "containerd://" else fixture["actual_ref"]))
            with self.subTest(prefix=prefix):
                self.accepted(fixture)

    def test_invalid_cached_content_or_platform_selection_never_accepts(self):
        for fault in ("config_platform", "foreign_layer", "artifact", "layer_encoding", "descriptor_size",
                      "descriptor_digest", "attestation_only", "ambiguous_platform", "missing_platform", "different_leaf"):
            with self.subTest(fault=fault):
                self.rejected(billing_fixture(fault=fault))
        for target in ("image", "actual_ref", "leaf_digest", "config_id"):
            fixture = billing_fixture()
            digest = fixture[target].split("@", 1)[-1]
            fixture["blobs"][digest] += " "
            with self.subTest(hash_mismatch=target):
                self.rejected(fixture)

    def test_every_cri_mapping_and_live_container_binding_is_required(self):
        for fault in ("pin_config", "pod_config", "pin_repodigest", "pod_repodigest", "cri_platform", "cri_rootfs",
                      "container_id", "state", "worker", "pod_uid", "pod_name", "namespace", "container_label",
                      "image_ref", "live_config", "requested_image", "kube_config", "kube_container", "foreign_repository",
                      "bare_digest", "missing_mapping"):
            fixture = billing_fixture()
            pin = fixture["images"][fixture["image"]]
            actual = fixture["images"][fixture["actual_ref"]]
            live = fixture["live"]["status"]
            status = fixture["pods"]["items"][0]["status"]["containerStatuses"][0]
            if fault in {"pin_config", "pod_config"}:
                (pin if fault == "pin_config" else actual)["status"]["id"] = "sha256:" + "f" * 64
            elif fault in {"pin_repodigest", "pod_repodigest"}:
                (pin if fault == "pin_repodigest" else actual)["status"]["repoDigests"] = [fixture["image"]]
            elif fault == "cri_platform":
                actual["info"]["imageSpec"]["architecture"] = "arm64"
            elif fault == "cri_rootfs":
                pin["info"]["imageSpec"]["rootfs"]["diff_ids"] = ["sha256:" + "f" * 64]
            elif fault == "container_id":
                live["id"] = "f" * 64
            elif fault == "state":
                live["state"] = "CONTAINER_EXITED"
            elif fault == "worker":
                live["metadata"]["name"] = "other"
            elif fault in {"pod_uid", "pod_name", "namespace", "container_label"}:
                key = {"pod_uid": "pod.uid", "pod_name": "pod.name", "namespace": "pod.namespace", "container_label": "container.name"}[fault]
                live["labels"]["io.kubernetes." + key] = "other"
            elif fault == "image_ref":
                live["imageRef"] = fixture["image"]
            elif fault == "live_config":
                live["image"]["image"] = "sha256:" + "f" * 64
            elif fault == "requested_image":
                live["image"]["userSpecifiedImage"] = fixture["actual_ref"]
            elif fault == "kube_config":
                status["image"] = "sha256:" + "f" * 64
            elif fault == "kube_container":
                status["containerID"] = "containerd://invalid"
            elif fault == "foreign_repository":
                status["imageID"] = fixture["actual_ref"].replace(REPOSITORY, "other.invalid/api")
            elif fault == "bare_digest":
                status["imageID"] = fixture["config_id"]
            else:
                del fixture["images"][fixture["actual_ref"]]
            with self.subTest(fault=fault):
                self.rejected(fixture)



if __name__ == "__main__":
    unittest.main()
