"""Target-code release retry launcher: no Docker/SSH/database calls."""

import importlib.util
import base64
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


class ResumeSourceTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).with_name("generation-reader-resume.py")
        self.assertTrue(path.exists(), "exact-target retry source loader absent")
        spec = importlib.util.spec_from_file_location("reader_resume", path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def archive(
        self, name="apps/api/src/yleum_api/__init__.py", *, revision=None, kind=None
    ):
        output = io.BytesIO()
        with tarfile.open(
            fileobj=output,
            mode="w",
            format=tarfile.PAX_FORMAT,
            pax_headers={"comment": revision or "a" * 40},
        ) as archive:
            item = tarfile.TarInfo(name)
            item.size = 9
            if kind is not None:
                item.type = kind
                item.linkname = "/outside"
            archive.addfile(item, io.BytesIO(b"value=17\n"))
        return output.getvalue()

    def test_target_package_is_extracted_without_ambient_source(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.module.extract_source(self.archive(), "a" * 40, Path(temp))
            self.assertEqual(result, Path(temp) / "apps/api/src")
            self.assertEqual(
                (result / "yleum_api/__init__.py").read_text(), "value=17\n"
            )

    def test_other_revision_and_non_source_members_deny(self):
        for payload in (
            self.archive(revision="b" * 40),
            self.archive("../outside"),
            self.archive("apps/api/.env"),
            self.archive("apps/api/data/sitecustomize.py"),
            self.archive("apps/api/skills/ui-ux-pro-max/scripts/run.py"),
            self.archive("/absolute"),
            self.archive("apps/api/src/../outside"),
        ):
            with (
                self.subTest(),
                tempfile.TemporaryDirectory() as temp,
                self.assertRaises(ValueError),
            ):
                self.module.extract_source(payload, "a" * 40, Path(temp))

    def test_links_devices_and_duplicate_members_deny(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE):
            with (
                self.subTest(kind=kind),
                tempfile.TemporaryDirectory() as temp,
                self.assertRaises(ValueError),
            ):
                self.module.extract_source(
                    self.archive(kind=kind), "a" * 40, Path(temp)
                )

    def test_bounded_input_and_unknown_context_deny(self):
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(ValueError):
            self.module.extract_source(
                b"x" * (self.module.MAX_ARCHIVE + 1), "a" * 40, Path(temp)
            )
        with self.assertRaises(ValueError):
            self.module.decode_context("not-base64", "a" * 40)

    def context(self):
        return {"version": 1, "release_sha": "a" * 40,
                "owner_nonce": "11111111-1111-4111-8111-111111111111",
                "core_capture_sha256": "b" * 64, "commerce_capture_sha256": "c" * 64}

    def test_duplicate_context_is_not_canonicalized_into_authority(self):
        raw = json.dumps(self.context())[:-1] + ',"version":1}'
        with self.assertRaises(ValueError):
            self.module.decode_context(base64.b64encode(raw.encode()).decode(), "a" * 40)

    def launch(self, program, context, *mode, assets=None):
        output = io.BytesIO()
        with tarfile.open(
            fileobj=output, mode="w", format=tarfile.PAX_FORMAT, pax_headers={"comment": "a" * 40}
        ) as archive:
            for name, text in {"__init__.py": "", "services/__init__.py": "",
                               "services/generation_deployment_drain.py": program}.items():
                data = text.encode()
                item = tarfile.TarInfo("apps/api/src/yleum_api/" + name)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
            for name, data in (assets or {}).items():
                item = tarfile.TarInfo(name)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
        return subprocess.run(
            [sys.executable, "-B", str(Path(__file__).with_name("generation-reader-resume.py")),
             "a" * 40, base64.b64encode(json.dumps(context).encode()).decode(), *mode],
            input=output.getvalue(), capture_output=True, timeout=10,
        )

    def test_target_can_read_its_bundled_api_design_tables(self):
        prefix = '''import json, sys
from pathlib import Path
api_root = Path(__file__).resolve().parents[3]
assert (api_root / "skills/ui-ux-pro-max/data/typography.csv").read_text() == "fixture"
assert (api_root / "data/design_patterns.json").read_text() == "fixture"
context = json.load(sys.stdin)
print(json.dumps(dict(context, basis="owned_release_capture", active=dict.fromkeys(
    ["generations","operations","leases","restorations"], 0), drained=True)))
'''
        result = self.launch(prefix, self.context(), assets={
            "apps/api/skills/ui-ux-pro-max/data/typography.csv": b"fixture",
            "apps/api/data/design_patterns.json": b"fixture",
        })
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fresh_process_imports_only_archived_target_and_sanitizes_failure(self):
        prefix = '''import json, sys
assert "reader-target-check-" in __file__
assert sys.argv[1:] == ["resume-check", "a" * 40]
context = json.load(sys.stdin)
'''
        programs = [
            (prefix + 'print(json.dumps({**context,"basis":"owned_release_capture","active":dict.fromkeys(["generations","operations","leases","restorations"],0),"drained":True}))', 0),
            (prefix + 'print(json.dumps({**context,"basis":"owned_release_capture","active":{"generations":1,"operations":0,"leases":0,"restorations":0},"drained":False})); raise SystemExit(75)', 75),
            (prefix + 'print("DO_NOT_EMIT_PRIVATE_PAYLOAD"); raise RuntimeError("DO_NOT_EMIT_PRIVATE_PAYLOAD")', 75),
        ]
        for program, expected in programs:
            with self.subTest(expected=expected):
                result = self.launch(program, self.context())
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertNotIn(b"DO_NOT_EMIT_PRIVATE_PAYLOAD", result.stdout + result.stderr)
                value = json.loads(result.stdout)
                self.assertEqual(value["drained"], expected == 0)

    def test_first_capture_does_not_accept_drain_without_exact_parked_proof(self):
        context = {"version": 1, "release_sha": "a" * 40,
                   "run_id": "11111111-1111-4111-8111-111111111111"}
        program = '''import json, sys
assert "reader-target-check-" in __file__
assert sys.argv[1:] == ["parked-source-check", "a" * 40]
context = json.load(sys.stdin)
assert context.pop("run_id") == "11111111-1111-4111-8111-111111111111"
result = dict(context, basis="parked_source_recovery", active=dict.fromkeys(
    ["generations", "operations", "leases", "restorations"], 0))
'''
        for selected, expected in ((True, 0), (False, 75)):
            with self.subTest(selected=selected):
                result = self.launch(
                    program + f'print(json.dumps(dict(result, selected_verified={selected}, '
                    'drained=True)))', context, "parked-source",
                )
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertNotIn(context["run_id"].encode(), result.stdout)
        invalid = self.launch(program, self.context(), "parked-source")
        self.assertEqual(invalid.returncode, 75)



    def expired_context(self):
        return {
            "version": 1, "release_sha": "a" * 40, "state_revision": 7,
            "run_id": "11111111-1111-4111-8111-111111111111",
            "attempt_id": "22222222-2222-4222-8222-222222222222",
            "source_sha256": "3" * 64, "context_sha256": "4" * 64, "clock_sha256": "5" * 64,
        }

    def test_expired_provider_eligibility_does_not_mean_drained(self):
        context = dict(self.expired_context(), reader_release_sha="b" * 40, mode="eligibility")
        program = '''import hashlib, json, sys
assert sys.argv[1:] == ["expired-provider-check", "a" * 40]
context = json.load(sys.stdin)
assert context["reader_release_sha"] == "b" * 40
digest = hashlib.sha256(json.dumps(context, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
result = dict(version=1, release_sha=context["release_sha"], mode=context["mode"],
              basis="expired_provider_reservation", scope_sha256=digest,
              active=dict(generations=1, operations=0, leases=0, restorations=0),
              eligible=True, drained=False)
'''
        valid = self.launch(program + "print(json.dumps(result))", context, "expired-provider")
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        self.assertFalse(json.loads(valid.stdout)["drained"])
        self.assertNotIn(context["run_id"].encode(), valid.stdout)
        for untrusted in (
            'result["drained"] = True', 'result["scope_sha256"] = "a" * 64',
            'result["active"]["generations"] = 0', 'result["active"]["operations"] = 1',
        ):
            outcome = self.launch(
                program + untrusted + "\nprint(json.dumps(result))", context, "expired-provider",
            )
            self.assertEqual(outcome.returncode, 75)
        for bad in (
            dict(context, reader_release_sha="a" * 40), dict(context, state_revision=True),
            dict(context, attempt_id="00000000-0000-0000-0000-000000000000"),
        ):
            self.assertEqual(self.launch(program, bad, "expired-provider").returncode, 75)

    def test_expired_provider_resume_requires_both_bound_captures_and_a_settled_result(self):
        context = self.expired_context() | self.context()
        program = '''import hashlib, json, sys
assert sys.argv[1:] == ["resume-expired-provider-check", "a" * 40]
context = json.load(sys.stdin)
assert context["core_capture_sha256"] == "b" * 64
assert context["commerce_capture_sha256"] == "c" * 64
digest = hashlib.sha256(json.dumps(context, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
result = dict(version=1, release_sha=context["release_sha"], mode="settled",
              basis="owned_expired_provider_capture", scope_sha256=digest,
              active=dict(generations=0, operations=0, leases=0, restorations=0),
              eligible=True, drained=True)
'''
        payload = program + "print(json.dumps(result))"
        valid = self.launch(payload, context, "resume-expired-provider")
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        self.assertTrue(json.loads(valid.stdout)["drained"])
        self.assertNotIn(context["owner_nonce"].encode(), valid.stdout)
        incomplete = dict(context)
        del incomplete["commerce_capture_sha256"]
        for bad in (incomplete, dict(context, mode="eligibility"), dict(context, reader_release_sha="b" * 40)):
            self.assertEqual(self.launch(payload, bad, "resume-expired-provider").returncode, 75)
        for untrusted in (
            'result["mode"] = "eligibility"', 'result["basis"] = "owned_release_capture"',
            'result["active"]["generations"] = 1',
        ):
            tampered = self.launch(
                program + untrusted + "\nprint(json.dumps(result))", context, "resume-expired-provider",
            )
            self.assertEqual(tampered.returncode, 75)
        self.assertEqual(self.launch(payload, context, "end-expired-provider").returncode, 75)


if __name__ == "__main__":
    unittest.main()
