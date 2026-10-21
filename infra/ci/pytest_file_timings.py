"""Opt-in CI telemetry. Never selects tests or changes their execution/outcomes."""

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath


def pytest_addoption(parser):
    parser.addoption("--file-timings-output", help="Fresh JSON report below RUNNER_TEMP")


def pytest_configure(config):
    output = config.getoption("--file-timings-output")
    if output is not None:
        config.pluginmanager.register(Recorder(config, Path(output)), "yleum-file-timings")


def regular_source(root, name):
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or len(path.parts) != 2
        or path.parts[0] != "tests"
        or not path.name.startswith("test_")
        or not path.name.endswith(".py")
    ):
        raise ValueError("timing source must be an explicit top-level API test file")
    source = root / name
    if source.is_symlink() or not source.is_file():
        raise ValueError("timing source must be a regular file")
    raw = source.read_bytes()
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


class Recorder:
    def __init__(self, config, output):
        scratch = Path(os.environ["RUNNER_TEMP"])
        if (
            not scratch.is_absolute()
            or scratch.resolve(strict=True) != scratch
            or not output.is_absolute()
            or ".." in output.parts
            or not output.is_relative_to(scratch)
            or output.parent == scratch
            or output.suffix != ".json"
        ):
            raise ValueError("timing output must be in a fresh owned RUNNER_TEMP subdirectory")
        for ancestor in (output.parent.parent, *output.parent.parent.parents):
            if ancestor.is_symlink():
                raise ValueError("redirected timing output")
        output.parent.mkdir(mode=0o700, exist_ok=False)
        self.output = output
        self.root = Path(config.rootpath)
        self.files = {}
        for name in config.args:
            if name in self.files:
                raise ValueError("duplicate timing source")
            self.files[name] = {
                **regular_source(self.root, name),
                "collected_tests": 0,
                "phase_seconds": dict.fromkeys(("setup", "call", "teardown"), 0.0),
                "reports": dict.fromkeys(("setup", "call", "teardown"), 0),
                "outcomes": dict.fromkeys(("passed", "failed", "skipped"), 0),
            }
        self.collected = 0

    def pytest_collection_finish(self, session):
        self.collected = len(session.items)
        for item in session.items:
            name = item.nodeid.split("::", 1)[0]
            self.files[name]["collected_tests"] += 1

    def pytest_runtest_logreport(self, report):
        name = report.nodeid.split("::", 1)[0]
        seconds = float(report.duration)
        if (
            report.when not in {"setup", "call", "teardown"}
            or not math.isfinite(seconds)
            or seconds < 0
        ):
            raise ValueError("invalid phase timing")
        entry = self.files[name]
        entry["phase_seconds"][report.when] += seconds
        entry["reports"][report.when] += 1
        entry["outcomes"][report.outcome] += 1

    def pytest_sessionfinish(self, session, exitstatus):
        for name, entry in self.files.items():
            entry["seconds"] = sum(entry["phase_seconds"].values())
            entry["source_unchanged"] = regular_source(self.root, name) == {
                "sha256": entry["sha256"],
                "bytes": entry["bytes"],
            }
        value = {
            "schema": 1,
            "status": "pytest_file_timings_finished",
            "source_revision": os.environ.get("GITHUB_SHA"),
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
            "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "pytest_exit_status": int(exitstatus),
            "collected_tests": self.collected,
            "files": self.files,
        }
        temporary = self.output.with_suffix(".tmp")
        raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as sink:
            sink.write(raw)
            sink.flush()
            os.fsync(sink.fileno())
        # Atomic create, including against a late collision; never overwrite evidence.
        os.link(temporary, self.output)
        temporary.unlink()
        if os.name == "posix":
            directory = os.open(self.output.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
