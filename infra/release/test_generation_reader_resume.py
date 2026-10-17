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
                output = io.BytesIO()
                with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT,
                                  pax_headers={"comment": "a" * 40}) as archive:
                    for name, text in {"__init__.py": "", "services/__init__.py": "",
                                       "services/generation_deployment_drain.py": program}.items():
                        data = text.encode()
                        item = tarfile.TarInfo("apps/api/src/yleum_api/" + name)
                        item.size = len(data)
                        archive.addfile(item, io.BytesIO(data))
                result = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("generation-reader-resume.py")),
                                         "a" * 40, base64.b64encode(json.dumps(self.context()).encode()).decode()],
                                        input=output.getvalue(), capture_output=True, timeout=10)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertNotIn(b"DO_NOT_EMIT_PRIVATE_PAYLOAD", result.stdout + result.stderr)
                value = json.loads(result.stdout)
                self.assertEqual(value["drained"], expected == 0)


if __name__ == "__main__":
    unittest.main()
