"""Offline contracts for the narrowly scoped publication PV grant."""
import copy
import importlib.util
import io
import json
import os
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

PATH = Path(__file__).with_name("publication-migration-access.py")
SHA = "a" * 40


class AccessContract(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PATH.exists(), "dedicated PV access helper is missing")
        previous_cwd = Path.cwd()
        self.addCleanup(os.chdir, previous_cwd)
        os.chdir(PATH.resolve().parents[2])
        spec = importlib.util.spec_from_file_location("pv_access", PATH)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.objects = {}
        self.calls = []
        self.created = []
        self.fail_create = 0
        self.bad_readback = False

    def existing(self):
        for obj in self.m.OBJECTS:
            self.objects[obj["kind"]] = self.stored(obj)

    def stored(self, obj):
        result = copy.deepcopy(obj)
        result["metadata"]["uid"] = "11111111-1111-4111-8111-111111111111"
        result["metadata"]["resourceVersion"] = "2"
        return result

    def run_command(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[0] == "git":
            return subprocess.CompletedProcess(argv, 0, SHA + "\n", "")
        self.assertEqual(argv[:3], ["kubectl", "--context", "max-runtime"])
        self.assertIn("--request-timeout=8s", argv)
        self.assertLessEqual(kwargs["timeout"], 12)
        if "create" in argv:
            obj = json.loads(kwargs["input"])
            self.created.append(obj)
            if self.fail_create == len(self.created):
                raise subprocess.TimeoutExpired(argv, 12, stderr="PRIVATE_TOKEN_URL")
            self.objects[obj["kind"]] = self.stored(obj)
            if self.bad_readback:
                self.objects[obj["kind"]]["metadata"]["uid"] = "bad"
            return subprocess.CompletedProcess(argv, 0, "", "")
        kind = argv[argv.index("get") + 1]
        if kind == "ServiceAccount":
            obj = {"apiVersion": "v1", "kind": kind, "metadata": {
                "name": "omnia-orchestrator", "namespace": "max-system",
                "uid": "22222222-2222-4222-8222-222222222222"}}
        else:
            obj = self.objects.get(kind)
        return subprocess.CompletedProcess(argv, 0, json.dumps(obj) if obj else "", "")

    def invoke(self, apply=False):
        args = ["--expected-revision", SHA] + (["--apply"] if apply else [])
        output = io.StringIO()
        with patch.object(self.m.subprocess, "run", self.run_command), redirect_stdout(output):
            status = self.m.main(args)
        self.assertNotIn("PRIVATE", output.getvalue())
        return status, json.loads(output.getvalue())

    def test_readonly_absence(self):
        self.assertEqual(self.invoke()[0], 1)
        self.assertFalse(self.created)

    def test_exact_existing_idempotent(self):
        self.existing()
        self.assertEqual(self.invoke(True)[0], 0)
        self.assertFalse(self.created)

    def test_create_and_readback(self):
        self.assertEqual(self.invoke(True)[0], 0)
        self.assertEqual(len(self.created), 2)
        first_create = next(i for i, call in enumerate(self.calls) if "create" in call)
        self.assertEqual(sum("get" in c for c in self.calls[:first_create]), 3)
        self.assertEqual(self.created[0]["rules"], [{"apiGroups": [""],
            "resources": ["persistentvolumes"], "verbs": ["get"]}])

    def test_drift_either_object_prevents_all_writes(self):
        for kind in ("ClusterRole", "ClusterRoleBinding"):
            with self.subTest(kind=kind):
                self.objects.clear()
                obj = next(o for o in self.m.OBJECTS if o["kind"] == kind)
                self.objects[kind] = self.stored(obj)
                self.objects[kind]["metadata"]["labels"]["extra"] = "unowned"
                self.assertEqual(self.invoke(True)[0], 1)
                self.assertFalse(self.created)

    def test_unknown_create_ack_no_retry(self):
        self.fail_create = 1
        self.assertEqual(self.invoke(True)[0], 1)
        self.assertEqual(len(self.created), 1)

    def test_partial_create_does_not_delete_or_retry(self):
        self.fail_create = 2
        self.assertEqual(self.invoke(True)[0], 1)
        self.assertEqual(len(self.created), 2)
        self.assertIn("ClusterRole", self.objects)
        self.assertFalse(any("delete" in c for c in self.calls))

    def test_bad_uid_readback_blocks_binding(self):
        self.bad_readback = True
        self.assertEqual(self.invoke(True)[0], 1)
        self.assertEqual(len(self.created), 1)

    def test_aggregation_or_extra_grant_fields_rejected(self):
        for field, value in (("aggregationRule", {"clusterRoleSelectors": [{}]}),
                             ("unknownGrant", True)):
            with self.subTest(field=field):
                self.objects.clear()
                self.existing()
                self.objects["ClusterRole"][field] = value
                self.assertEqual(self.invoke(True)[0], 1)
                self.assertFalse(self.created)

    def test_role_ref_subject_and_rules_drift_rejected(self):
        for kind, field, value in (("ClusterRole", "rules", []),
                                  ("ClusterRoleBinding", "roleRef", {}),
                                  ("ClusterRoleBinding", "subjects", [])):
            with self.subTest(field=field):
                self.existing()
                self.objects[kind][field] = value
                self.assertEqual(self.invoke(True)[0], 1)
                self.assertFalse(self.created)

    def test_failed_get_never_treated_as_absent_and_private_error_hidden(self):
        def failed(argv, **kwargs):
            if argv[0] == "git":
                return subprocess.CompletedProcess(argv, 0, SHA, "")
            return subprocess.CompletedProcess(argv, 1, "PRIVATE_URL", "PRIVATE_TOKEN")
        self.run_command = failed
        self.assertEqual(self.invoke(True), (1, {"status": "blocked", "error_code": "command_failed"}))
        self.assertFalse(self.created)

    def test_revision_mismatch_prevents_kubernetes_access(self):
        def wrong(argv, **kwargs):
            self.assertEqual(argv, ["git", "rev-parse", "HEAD"])
            return subprocess.CompletedProcess(argv, 0, "b" * 40, "")
        self.run_command = wrong
        self.assertEqual(self.invoke(True)[1]["error_code"], "revision_guard_failed")

    def test_uid_replacement_is_not_idempotent_success(self):
        self.existing()
        original = self.run_command
        reads = 0

        def replaced(argv, **kwargs):
            nonlocal reads
            if "get" in argv and "ClusterRole" in argv:
                reads += 1
                if reads == 2:
                    self.objects["ClusterRole"]["metadata"]["uid"] = "33333333-3333-4333-8333-333333333333"
            return original(argv, **kwargs)
        self.run_command = replaced
        self.assertEqual(self.invoke(True)[1]["error_code"], "uid_changed")


if __name__ == "__main__":
    unittest.main()
