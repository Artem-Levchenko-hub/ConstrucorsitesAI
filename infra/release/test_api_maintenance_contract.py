"""Release maintenance retains live API authority without entering reader cgroups.

All process, Docker and namespace I/O below is mocked. Shell tests execute only
extracted functions with inert git/ssh doubles, never the deployment script.
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import unittest
from contextlib import ExitStack, contextmanager, nullcontext, redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
CURRENT = "a" * 40
TARGET = "b" * 40
CONTAINER = "c" * 64


def load_source(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve postponed annotations through the module registry.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def wire(value):
    return base64.b64encode(json.dumps(value).encode()).decode()


def shell_function(name):
    source = (HERE / "deploy-prod.sh").read_text(encoding="utf-8")
    return name + "() {" + source.split(name + "() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"


def bash_executable():
    # Prefer Git Bash to the Windows WSL launcher, which can exist without WSL.
    if os.name == "nt":
        candidate = Path("C:/Program Files/Git/bin/bash.exe")
        return str(candidate) if candidate.is_file() else None
    return shutil.which("bash")


BASH = bash_executable()


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.helper = load_source("api_maintenance_contract", "api-maintenance.py")

    def runtime(self):
        return self.helper.Runtime(
            CONTAINER, "sha256:" + "d" * 64, 1234, 5678,
            222, 333, 444, (44, 555),
            {"OMNIA_RELEASE_SHA": CURRENT, "PATH": "/app/.venv/bin:/usr/bin",
             "LIVE_VALUE": "spaces 'quotes' = and\nnewlines"},
        )

    def docker_row(self):
        return {
            "Id": CONTAINER, "Image": "sha256:" + "d" * 64,
            "Name": "/yleum-prod-api",
            "State": {"Running": True, "Paused": False, "Dead": False, "Pid": 1234},
            "Config": {"Labels": {"com.docker.compose.service": "api"},
                       "Env": [f"OMNIA_RELEASE_SHA={CURRENT}", "VALUE=a=b 'quoted'"]},
        }

    @contextmanager
    def inspected(self, row=None, *, actual=None, groups=None, status="NSpid:\t1234\t1\n", host_pid_inode=666):
        row = self.docker_row() if row is None else row
        actual = (f"OMNIA_RELEASE_SHA={CURRENT}\0OTHER=value\0".encode()
                  if actual is None else actual)
        groups = ((f"/system.slice/docker-{CONTAINER}.scope",),
                  ("/system.slice/sshd.service/maintenance",)) if groups is None else groups

        def metadata(path):
            name = str(path).replace("\\", "/")
            return {
                "/proc/1234/root": SimpleNamespace(st_dev=44, st_ino=555),
                "/proc/1234/ns/mnt": SimpleNamespace(st_ino=222),
                "/proc/1234/ns/net": SimpleNamespace(st_ino=333),
                "/proc/1234/ns/pid": SimpleNamespace(st_ino=444),
                "/proc/self/ns/pid": SimpleNamespace(st_ino=host_pid_inode),
            }[name]

        with ExitStack() as stack:
            command = stack.enter_context(patch.object(
                self.helper.subprocess, "run", return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps([row]))))
            stack.enter_context(patch.object(self.helper, "process_start", return_value=5678))
            stack.enter_context(patch.object(self.helper, "cgroups", side_effect=groups))
            read = stack.enter_context(patch.object(self.helper.Path, "read_bytes", autospec=True,
                                                   return_value=actual))
            stack.enter_context(patch.object(self.helper.Path, "read_text", return_value=status))
            stack.enter_context(patch.object(self.helper.Path, "stat", autospec=True,
                                             side_effect=metadata))
            yield command, read

    @contextmanager
    def linux_run(self, *, runtimes=None, child_status=0):
        runtime = self.runtime()
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.helper.sys, "platform", "linux"))
            stack.enter_context(patch.object(self.helper.os, "geteuid", create=True, return_value=0))
            for name, value in (("O_CLOEXEC", 0x80000), ("O_PATH", 0x200000),
                                ("O_DIRECTORY", 0x10000)):
                stack.enter_context(patch.object(self.helper.os, name, value, create=True))
            launcher = stack.enter_context(patch.object(
                self.helper.Path, "stat", autospec=True, return_value=SimpleNamespace(
                    st_mode=stat.S_IFREG | 0o755, st_uid=0)))
            inspect = stack.enter_context(patch.object(
                self.helper, "inspect_runtime", side_effect=runtimes or [runtime] * 3))
            pidfd = stack.enter_context(patch.object(self.helper.os, "pidfd_open", create=True,
                                                     return_value=101))
            opened = stack.enter_context(patch.object(self.helper.os, "open", side_effect=[102, 103, 104, 105]))
            closed = stack.enter_context(patch.object(self.helper.os, "close"))
            fstat = stack.enter_context(patch.object(self.helper.os, "fstat", side_effect=[
                SimpleNamespace(st_ino=222), SimpleNamespace(st_ino=333), SimpleNamespace(st_ino=444),
                SimpleNamespace(st_dev=44, st_ino=555)]))
            alive = stack.enter_context(patch.object(self.helper, "alive"))
            child = stack.enter_context(patch.object(self.helper, "execute_command", return_value=child_status))
            yield SimpleNamespace(runtime=runtime, inspect=inspect, pidfd=pidfd, opened=opened,
                                  closed=closed, fstat=fstat, alive=alive, child=child, launcher=launcher)

    def assert_all_closed(self, fixture):
        self.assertEqual([call.args[0] for call in fixture.closed.call_args_list], [105, 104, 103, 102, 101])

    def test_wire_round_trip_preserves_code_quotes_unicode_and_argv_boundaries(self):
        arguments = ["-c", 'print("spaces and apostrophe\'s")\n', "", "x y", "x'\"$`\\", "Ядро"]
        self.assertEqual(self.helper.decode_arguments(wire([CURRENT, *arguments])), (CURRENT, arguments))
        for mode in ("-", "-m"):
            with self.subTest(mode=mode):
                self.assertEqual(self.helper.decode_arguments(wire([CURRENT, mode, "operand"])),
                                 (CURRENT, [mode, "operand"]))
        self.assertEqual(self.helper.decode_arguments(wire(["", "--describe-runtime"])), (None, []))

    def test_wire_rejects_invalid_encoding_shape_revision_and_modes(self):
        malformed = ["!not-base64!", base64.b64encode(b"not JSON").decode(),
                     "A" * (self.helper.MAX_ARGUMENTS + 1)]
        malformed += [wire(value) for value in (
            {}, [], [CURRENT], [CURRENT, 1], [CURRENT, True], [CURRENT, None],
            [CURRENT, ["-"]], [CURRENT, "-", "a\0b"], ["A" * 40, "-"],
            ["a" * 39, "-"], [CURRENT, "script.py"], [CURRENT, "--"],
            [CURRENT, "-I"], [CURRENT, "-"] + [""] * 63,
            ["", "-"], ["", "--describe-runtime", "-"], [CURRENT, "--describe-runtime"],
        )]
        for encoded in malformed:
            with self.subTest(encoded=encoded[:70]):
                with self.assertRaises((ValueError, TypeError)):
                    self.helper.decode_arguments(encoded)

    def test_inspect_binds_live_pid_namespaces_environment_and_container(self):
        with self.inspected() as (command, read):
            runtime = self.helper.inspect_runtime(CURRENT)
        self.assertEqual(runtime, replace(self.runtime(), environment={
            "OMNIA_RELEASE_SHA": CURRENT, "VALUE": "a=b 'quoted'"}))
        command.assert_called_once_with(["docker", "inspect", "yleum-prod-api"],
                                        capture_output=True, text=True, timeout=15)
        self.assertEqual(str(read.call_args.args[0]).replace("\\", "/"), "/proc/1234/environ")

    def test_inspect_rejects_wrong_runtime_identity_state_label_and_duplicate_env(self):
        mutations = [
            ("name", lambda row: row.update(Name="/another-api")),
            ("id", lambda row: row.update(Id="c" * 63)),
            ("image", lambda row: row.update(Image="omnia-api:tag")),
            ("running", lambda row: row["State"].update(Running=False)),
            ("numeric_running", lambda row: row["State"].update(Running=1)),
            ("paused", lambda row: row["State"].update(Paused=True)),
            ("dead", lambda row: row["State"].update(Dead=True)),
            ("pid_bool", lambda row: row["State"].update(Pid=True)),
            ("pid_init", lambda row: row["State"].update(Pid=1)),
            ("pid_string", lambda row: row["State"].update(Pid="1234")),
            ("foreign_service", lambda row: row["Config"].update(Labels={"com.docker.compose.service": "worker"})),
            ("missing_label", lambda row: row["Config"].update(Labels=None)),
            ("wrong_revision", lambda row: row["Config"].update(Env=[f"OMNIA_RELEASE_SHA={TARGET}"])),
            ("duplicate_env", lambda row: row["Config"]["Env"].append("VALUE=second")),
            ("duplicate_revision", lambda row: row["Config"]["Env"].append(f"OMNIA_RELEASE_SHA={CURRENT}")),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name):
                row = self.docker_row()
                mutate(row)
                with self.inspected(row), self.assertRaises(ValueError):
                    self.helper.inspect_runtime(CURRENT)

    def test_actual_pid_revision_must_match_once(self):
        for actual in (b"OTHER=1\0", f"OMNIA_RELEASE_SHA={TARGET}\0".encode(),
                       (f"OMNIA_RELEASE_SHA={CURRENT}\0" * 2).encode()):
            with self.subTest(actual=actual), self.inspected(actual=actual), self.assertRaises(ValueError):
                self.helper.inspect_runtime(CURRENT)

    def test_serving_pid_must_be_private_namespace_init(self):
        for status in ("Name:\tpython\n", "NSpid:\t1234\t2\n", "NSpid:\t1234\t1\nNSpid:\t1234\t1\n"):
            with self.subTest(status=status), self.inspected(status=status), self.assertRaises(ValueError):
                self.helper.inspect_runtime(CURRENT)
        with self.inspected(host_pid_inode=444), self.assertRaises(ValueError):
            self.helper.inspect_runtime(CURRENT)

    def test_describe_runtime_requires_real_identity_without_http_readiness(self):
        with self.inspected():
            self.assertEqual(self.helper.inspect_runtime(None).environment["OMNIA_RELEASE_SHA"], CURRENT)
        row = self.docker_row()
        row["Config"]["Env"] = ["OMNIA_RELEASE_SHA=not-a-revision"]
        with self.inspected(row), self.assertRaises(ValueError):
            self.helper.inspect_runtime(None)
        output = io.StringIO()
        with patch.object(sys, "argv", ["maintenance", wire(["", "--describe-runtime"])]), \
                patch.object(self.helper, "pinned_runtime", return_value=nullcontext((self.runtime(), ()))) as pinned, \
                patch.object(self.helper, "execute_command") as execute, redirect_stdout(output):
            self.assertEqual(self.helper.main(), 0)
            pinned.assert_called_once_with(None)
            execute.assert_not_called()
        self.assertEqual(output.getvalue(), CURRENT + "\n")

    def test_runtime_requires_actual_container_cgroup_and_external_launcher(self):
        supervisor = f"/system.slice/docker-{CONTAINER}.scope"
        for groups in ((("/wrong/container",), ("/outside",)),
                       ((supervisor,), (supervisor,)),
                       ((supervisor,), (supervisor + "/child",))):
            with self.subTest(groups=groups), self.inspected(groups=groups), self.assertRaises(ValueError):
                self.helper.inspect_runtime(CURRENT)
        self.assertTrue(self.helper.outside_supervisor((supervisor + "-other",), (supervisor,)))

    def test_inspect_command_failure_malformed_or_duplicate_rows_never_authorizes(self):
        for result in (SimpleNamespace(returncode=1, stdout="[]"),
                       SimpleNamespace(returncode=0, stdout="not-json"),
                       SimpleNamespace(returncode=0, stdout="[]"),
                       SimpleNamespace(returncode=0, stdout=json.dumps([self.docker_row()] * 2)),
                       SimpleNamespace(returncode=0, stdout=" " * (1024 * 1024 + 1))):
            with self.subTest(length=len(result.stdout)), patch.object(
                self.helper.subprocess, "run", return_value=result
            ) as command, self.assertRaises(ValueError):
                self.helper.inspect_runtime(CURRENT)
            command.assert_called_once()

    def test_proc_start_and_cgroup_parsing_use_pinned_host_pid(self):
        with patch.object(self.helper.Path, "read_text", return_value="1234 (a ) spaced) " +
                          " ".join(["S"] + ["0"] * 18 + ["5678", "tail"])):
            self.assertEqual(self.helper.process_start(1234), 5678)
        with patch.object(self.helper.Path, "read_text", return_value="0::/system.slice/maintenance\n"):
            self.assertEqual(self.helper.cgroups("self"), ("/system.slice/maintenance",))
        for content in ("", "0::relative"):
            with self.subTest(content=content), patch.object(self.helper.Path, "read_text", return_value=content), \
                    self.assertRaises(ValueError):
                self.helper.cgroups(1234)

    def test_linux_run_uses_pinned_mount_net_root_and_only_real_environment(self):
        arguments = ["-c", "import sys; print(sys.argv)", "space ' quote"]
        with self.linux_run(child_status=75) as fixture, patch.dict(os.environ, {"HOST_ONLY_SECRET": "never pass"}):
            self.assertEqual(self.helper.run(CURRENT, arguments), 75)
            command = fixture.child.call_args.args[0]
            self.assertEqual(command, ["/usr/bin/nsenter",
                "--mount=/proc/self/fd/102", "--net=/proc/self/fd/103",
                "--pid=/proc/self/fd/104", "--root=/proc/self/fd/105",
                "--wd=/proc/self/fd/105/app", "--",
                "/app/.venv/bin/python", *arguments])
            self.assertEqual(fixture.child.call_args.args[1:], (fixture.runtime.environment, (102, 103, 104, 105)))
            self.assertEqual(fixture.child.call_args.kwargs, {})
            self.assertNotIn("HOST_ONLY_SECRET", fixture.child.call_args.args[1])
            self.assertFalse(any(arg.startswith("--cgroup") or arg == "--no-fork" for arg in command))
            self.assertEqual(fixture.inspect.call_count, 3)
            self.assertTrue(all(call.args == (CURRENT,) for call in fixture.inspect.call_args_list))
            self.assertEqual(fixture.alive.call_args_list[0].args, (101,))
            self.assertEqual(fixture.alive.call_count, 2)
            self.assertEqual([call.args[0] for call in fixture.opened.call_args_list],
                             ["/proc/1234/ns/mnt", "/proc/1234/ns/net", "/proc/1234/ns/pid", "/proc/1234/root"])
            root_flags = fixture.opened.call_args_list[-1].args[1]
            self.assertTrue(root_flags & self.helper.os.O_PATH)
            self.assertTrue(root_flags & self.helper.os.O_DIRECTORY)
            self.assertTrue(all(call.args[1] & self.helper.os.O_CLOEXEC
                                for call in fixture.opened.call_args_list))
        self.assert_all_closed(fixture)

    def test_replacement_before_or_after_child_denies_without_retry_or_fallback(self):
        original = self.runtime()
        for changed in (replace(original, container_id="e" * 64),
                        replace(original, image_id="sha256:" + "e" * 64),
                        replace(original, start_ticks=5679), replace(original, pid=5679),
                        replace(original, pid_inode=999), replace(original, mount_inode=999),
                        replace(original, network_inode=999), replace(original, root_identity=(44, 999)),
                        replace(original, environment={**original.environment, "CHANGED": "yes"})):
            for after in (False, True):
                sequence = [original, original, changed] if after else [original, changed]
                with self.subTest(changed=changed, after=after), self.linux_run(runtimes=sequence) as fixture:
                    with self.assertRaises(ValueError):
                        self.helper.run(CURRENT, ["-m", "module"])
                    self.assertEqual(fixture.child.call_count, int(after))
                self.assert_all_closed(fixture)

    def test_wrong_pinned_descriptor_identity_denies_before_execution(self):
        for values in ([SimpleNamespace(st_ino=999)],
                       [SimpleNamespace(st_ino=222), SimpleNamespace(st_ino=999)],
                       [SimpleNamespace(st_ino=222), SimpleNamespace(st_ino=333),
                        SimpleNamespace(st_ino=999)],
                       [SimpleNamespace(st_ino=222), SimpleNamespace(st_ino=333), SimpleNamespace(st_ino=444),
                        SimpleNamespace(st_dev=44, st_ino=999)]):
            with self.subTest(values=values), self.linux_run() as fixture:
                fixture.fstat.side_effect = values
                with self.assertRaises(ValueError):
                    self.helper.run(CURRENT, ["-"])
                fixture.child.assert_not_called()
            self.assert_all_closed(fixture)

    def test_timeout_and_nonzero_child_status_are_not_retried(self):
        with self.linux_run() as fixture:
            fixture.child.side_effect = subprocess.TimeoutExpired("nsenter", 180)
            with self.assertRaises(subprocess.TimeoutExpired):
                self.helper.run(CURRENT, ["-"])
            fixture.child.assert_called_once()
            self.assertEqual(fixture.inspect.call_count, 2)
        self.assert_all_closed(fixture)
        for status in (1, 75, 127):
            with self.subTest(status=status), self.linux_run(child_status=status) as fixture:
                self.assertEqual(self.helper.run(CURRENT, ["-"]), status)
                fixture.child.assert_called_once()
            self.assert_all_closed(fixture)

    def test_partial_open_and_liveness_failure_close_owned_descriptors(self):
        with self.linux_run() as fixture:
            fixture.opened.side_effect = [102, OSError("vanished namespace")]
            with self.assertRaises(OSError):
                self.helper.run(CURRENT, ["-"])
            self.assertEqual([call.args[0] for call in fixture.closed.call_args_list], [102, 101])
            fixture.child.assert_not_called()
        for after in (False, True):
            with self.subTest(after=after), self.linux_run() as fixture:
                fixture.alive.side_effect = [None, ValueError()] if after else [ValueError()]
                with self.assertRaises(ValueError):
                    self.helper.run(CURRENT, ["-"])
                self.assertEqual(fixture.child.call_count, int(after))
            self.assert_all_closed(fixture)

    def test_nonlinux_nonroot_and_untrusted_nsenter_fail_before_runtime(self):
        for platform, uid in (("win32", 0), ("linux", 1000)):
            with self.subTest(platform=platform, uid=uid), self.linux_run() as fixture:
                with patch.object(self.helper.sys, "platform", platform), patch.object(self.helper.os, "geteuid", return_value=uid):
                    with self.assertRaises(ValueError):
                        self.helper.run(CURRENT, ["-"])
                fixture.inspect.assert_not_called()
                fixture.child.assert_not_called()
        for uid, mode in ((1000, stat.S_IFREG | 0o755), (0, stat.S_IFREG | 0o777),
                          (0, stat.S_IFDIR | 0o755)):
            with self.subTest(uid=uid, mode=mode), self.linux_run() as fixture:
                fixture.launcher.return_value = SimpleNamespace(st_uid=uid, st_mode=mode)
                with self.assertRaises(ValueError):
                    self.helper.run(CURRENT, ["-"])
                fixture.inspect.assert_not_called()

    def test_pidfd_reports_terminated_process(self):
        poll = SimpleNamespace(register=lambda *_: None, poll=lambda _: [(101, 1)])
        with patch.object(self.helper.select, "poll", create=True, return_value=poll), \
                patch.object(self.helper.select, "POLLIN", 1, create=True), self.assertRaises(ValueError):
            self.helper.alive(101)

    def test_main_propagates_child_exit_and_cli_sanitizes_failure(self):
        with patch.object(sys, "argv", ["maintenance", wire([CURRENT, "-", "space ' quote"])]), \
                patch.object(self.helper, "run", return_value=75) as run:
            self.assertEqual(self.helper.main(), 75)
            run.assert_called_once_with(CURRENT, ["-", "space ' quote"])
        result = subprocess.run([sys.executable, str(HERE / "api-maintenance.py"),
                                 "malformed-secret-never-log!"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 75)
        self.assertEqual(result.stdout, "")
        diagnostic = json.loads(result.stderr)
        self.assertEqual(set(diagnostic), {"maintenance_failed", "error_class"})
        self.assertIs(diagnostic["maintenance_failed"], True)
        self.assertNotIn("secret", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_execute_starts_one_owned_session_with_inherited_streams_and_exact_environment(self):
        environment = {"OMNIA_RELEASE_SHA": CURRENT, "ACTUAL": "not host"}
        descriptors = (102, 103, 104, 105)
        command = self.helper.namespace_command(descriptors, ["-", "space ' quote"])
        child = MagicMock()
        child.wait.return_value = 75
        context = MagicMock()
        context.__enter__.return_value = child
        with patch.object(self.helper.subprocess, "Popen", return_value=context) as popen, \
                patch.object(self.helper, "stop_command") as stop:
            self.assertEqual(self.helper.execute_command(command, environment, descriptors), 75)
            popen.assert_called_once_with(command, env=environment, pass_fds=descriptors,
                                          start_new_session=True)
            child.wait.assert_called_once_with(timeout=180)
            stop.assert_not_called()
        context.__exit__.assert_called_once()

    def test_execute_timeout_or_cancellation_stops_session_and_never_retries(self):
        for failure in (subprocess.TimeoutExpired("nsenter", 180), KeyboardInterrupt(), RuntimeError("cancelled")):
            with self.subTest(failure=type(failure).__name__):
                child = MagicMock()
                child.wait.side_effect = failure
                context = MagicMock()
                context.__enter__.return_value = child
                with patch.object(self.helper.subprocess, "Popen", return_value=context) as popen, \
                        patch.object(self.helper, "stop_command") as stop, self.assertRaises(type(failure)):
                    self.helper.execute_command(["nsenter"], {"ACTUAL": "value"}, (102,), timeout=7)
                popen.assert_called_once()
                child.wait.assert_called_once_with(timeout=7)
                stop.assert_called_once_with(child)
                context.__exit__.assert_called_once()

    def test_stop_kills_whole_owned_group_even_if_leader_would_exit_on_term(self):
        child = MagicMock(pid=12345)
        calls = MagicMock()
        calls.attach_mock(child.wait, "wait")
        with patch.object(self.helper.os, "killpg", create=True) as kill, \
                patch.object(self.helper.signal, "SIGKILL", 9, create=True):
            calls.attach_mock(kill, "killpg")
            self.helper.stop_command(child)
            kill.assert_called_once_with(12345, 9)
            child.wait.assert_called_once_with(timeout=5)
        self.assertEqual([call[0] for call in calls.mock_calls], ["killpg", "wait"])

    def test_stop_already_gone_owned_group_still_reaps_child(self):
        child = MagicMock(pid=12345)
        with patch.object(self.helper.os, "killpg", create=True, side_effect=ProcessLookupError) as kill, \
                patch.object(self.helper.signal, "SIGKILL", 9, create=True):
            self.helper.stop_command(child)
            kill.assert_called_once_with(12345, 9)
            child.wait.assert_called_once_with(timeout=5)


class ReaderBoundaryTests(unittest.TestCase):
    def test_real_guard_denies_same_maintenance_python_only_inside_reader_cgroup(self):
        observer = load_source("api_maintenance_observer", "generation-reader-inventory.py")
        argv = ["/app/.venv/bin/python", "-m", "yleum_api.services.generation_deployment_drain", "check", TARGET]
        self.assertIsNone(observer.process_role(argv))
        supervisor = f"/system.slice/docker-{CONTAINER}.scope"
        for group in (supervisor, supervisor + "/child"):
            row = {"role": None, "argv": argv, "cgroup": "0::" + group,
                   "host_pid": 9000, "parent": 1}
            with self.subTest(group=group), self.assertRaises(ValueError):
                observer.require_no_hidden_readers({9000: row}, [supervisor])
        row = {"role": None, "argv": argv, "cgroup": "0::/system.slice/sshd.service/maintenance",
               "host_pid": 9000, "parent": 1}
        observer.require_no_hidden_readers({9000: row}, [supervisor])


@unittest.skipUnless(BASH, "a real local bash is required")
class MaintenanceShellTests(unittest.TestCase):
    def execute(self, script, *, arguments=(), stdin=b""):
        python = shlex.quote(sys.executable.replace("\\", "/"))
        prefix = "set -euo pipefail\npython3() { " + python + ' "$@"; }\n'
        return subprocess.run([BASH, "-c", prefix + script, "maintenance-test", *arguments],
                              input=stdin, capture_output=True, timeout=15,
                              env={**os.environ, "MSYS_NO_PATHCONV": "1", "MSYS2_ARG_CONV_EXCL": "*"})

    def test_real_shell_wire_retains_binary_stdin_and_separate_quoted_arguments(self):
        launcher = (
            "import base64,json,sys\n"
            "print(json.dumps({'wire':json.loads(base64.b64decode(sys.argv[1],validate=True)),"
            "'stdin':base64.b64encode(sys.stdin.buffer.read()).decode()}))\n"
        )
        git_stub = (
            "import sys\n"
            f"assert sys.argv[1:]==['-C','/frozen/repo','show','{TARGET}:infra/release/api-maintenance.py']\n"
            f"sys.stdout.write({launcher!r})\n"
        )
        ssh_stub = (
            "import shlex,sys\n"
            "assert sys.argv[1]=='max-core' and len(sys.argv)==3\n"
            "args=shlex.split(sys.argv[2])\n"
            "assert args[:4]==['sudo','-n','python3','-c'] and len(args)==6\n"
            "sys.argv=['release-api-maintenance',args[5]]\n"
            "exec(compile(args[4],'inert-remote-launcher','exec'),{'__name__':'__main__'})\n"
        )
        script = f"REPO=/frozen/repo\nSHA={TARGET}\nAPI_RUNTIME_SHA={CURRENT}\n"
        script += "git() { python3 -c " + shlex.quote(git_stub) + ' "$@"; }\n'
        script += "ssh() { python3 -c " + shlex.quote(ssh_stub) + ' "$@"; }\n'
        arguments = ("-c", "print('do not execute this')\n", "", "a b", "'\"$`\\", "Ядро")
        script += shell_function("api_maintenance")
        # Set argv inside POSIX shell, avoiding Windows-to-MSYS argv rewriting.
        script += "\nset -- " + shlex.join(arguments) + '\napi_maintenance "$@"\n'
        payload = bytes(range(256)) + b"\n'quoted'\x00tail\n"
        result = self.execute(script, stdin=payload)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        decoded = json.loads(result.stdout)
        self.assertEqual(decoded["wire"], [CURRENT, *arguments])
        self.assertEqual(base64.b64decode(decoded["stdin"], validate=True), payload)

    def test_transport_failure_is_propagated_once_without_fallback(self):
        script = f"REPO=/repo\nSHA={TARGET}\nAPI_RUNTIME_SHA={CURRENT}\n"
        script += "git() { printf 'pass'; }\nssh() { printf 'attempt\\n' >&2; return 75; }\n"
        script += shell_function("api_maintenance") + "\napi_maintenance -\n"
        result = self.execute(script)
        self.assertEqual(result.returncode, 75)
        self.assertEqual(result.stderr, b"attempt\n")

    def test_unavailable_committed_launcher_never_calls_transport(self):
        script = f"REPO=/repo\nSHA={TARGET}\nAPI_RUNTIME_SHA={CURRENT}\n"
        script += "git() { return 99; }\nssh() { printf forbidden; return 0; }\n"
        script += shell_function("api_maintenance") + "\napi_maintenance -\n"
        result = self.execute(script)
        self.assertEqual(result.returncode, 99)
        self.assertEqual(result.stdout, b"")

    def test_database_check_keeps_owner_and_target_bootstrap_payload(self):
        script = f"SHA={TARGET}\nREPO=/repo\nBOOTSTRAP=1\nREADER_RETRY=0\n"
        git_stub = (
            "import sys\n"
            f"assert sys.argv[1:]==['-C','/repo','show','{TARGET}:apps/api/src/yleum_api/services/generation_deployment_drain.py']\n"
            "print('exact-target-bootstrap-bytes',end='')\n"
        )
        script += "git() { python3 -c " + shlex.quote(git_stub) + ' "$@"; }\n'
        script += "ingress_drain() { [ \"$1\" = check ]; }\n"
        capture = "import json,sys; print(json.dumps({'argv':sys.argv[1:],'stdin':sys.stdin.read()}))"
        script += "api_maintenance() { python3 -c " + shlex.quote(capture) + ' "$@"; }\n'
        script += shell_function("database_quiescence") + "\ndatabase_quiescence\nBOOTSTRAP=0\ndatabase_quiescence\n"
        result = self.execute(script)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        bootstrap, ordinary = map(json.loads, result.stdout.splitlines())
        self.assertEqual(bootstrap, {"argv": ["-", "bootstrap-check", TARGET], "stdin": "exact-target-bootstrap-bytes"})
        self.assertEqual(ordinary, {"argv": ["-m", "yleum_api.services.generation_deployment_drain", "check", TARGET], "stdin": ""})

    def test_resume_keeps_target_archive_loader_and_exact_capture_context(self):
        nonce = "11111111-1111-4111-8111-111111111111"
        owner = {"version": 1, "release_sha": TARGET, "owner_nonce": nonce}
        core = json.dumps({"owner": owner, "capture_sha256": "c" * 64})
        commerce = json.dumps({"owner": owner, "capture_sha256": "d" * 64})
        script = f"SHA={TARGET}\nREPO=/repo\nREADER_NONCE={nonce}\n"
        control_stub = (
            "import sys\n"
            "host=sys.argv[1]\n"
            f"assert host in ('core','commerce') and sys.argv[2:]==['capture-context','--release','{TARGET}','--owner-nonce','{nonce}']\n"
            f"print({core!r} if host=='core' else {commerce!r})\n"
        )
        git_stub = (
            "import sys\n"
            "args=sys.argv[1:]\n"
            f"if args==['-C','/repo','archive','--format=tar','{TARGET}','apps/api/src']:\n"
            " print('target-package-archive',end='')\n"
            f"elif args==['-C','/repo','show','{TARGET}:infra/release/generation-reader-resume.py']:\n"
            " print('target-resume-loader',end='')\n"
            "else: raise AssertionError('unexpected source request')\n"
        )
        script += "reader_control() { python3 -c " + shlex.quote(control_stub) + ' "$@"; }\n'
        script += "git() { python3 -c " + shlex.quote(git_stub) + ' "$@"; }\n'
        capture = "import json,sys; print(json.dumps({'argv':sys.argv[1:],'stdin':sys.stdin.read()}))"
        script += "api_maintenance() { python3 -c " + shlex.quote(capture) + ' "$@"; }\n'
        script += shell_function("reader_resume_check") + "\nreader_resume_check\n"
        result = self.execute(script)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        decoded = json.loads(result.stdout)
        self.assertEqual(decoded["stdin"], "target-package-archive")
        arguments = decoded["argv"]
        self.assertEqual(arguments[0], "-c")
        self.assertIn(base64.b64encode(b"target-resume-loader").decode(), arguments[1])
        self.assertEqual(arguments[2], TARGET)
        context = json.loads(base64.b64decode(arguments[3], validate=True))
        self.assertEqual(context, {**owner, "core_capture_sha256": "c" * 64, "commerce_capture_sha256": "d" * 64})

    def test_current_reader_gate_and_target_captures_keep_distinct_revisions(self):
        script = f"SHA={TARGET}\nAPI_RUNTIME_SHA={CURRENT}\nREADER_RETRY=0\n"
        script += "ssh() { printf 1; }\n"
        capture = "import json,sys; print(json.dumps(sys.argv[1:]))"
        script += "reader_control() { python3 -c " + shlex.quote(capture) + ' "$@"; }\n'
        script += shell_function("reader_before") + "\nreader_before\n"
        result = self.execute(script)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        current, core, commerce = map(json.loads, result.stdout.splitlines())
        self.assertEqual(current, ["core", "verify-current", "--release", CURRENT])
        self.assertEqual(core[:5], ["core", "capture", "--release", TARGET, "--owner-nonce"])
        self.assertEqual(commerce, ["commerce", *core[1:]])


class CanonicalDeployContractTests(unittest.TestCase):
    def test_all_serving_api_python_calls_are_external_to_reader_cgroups(self):
        source = (HERE / "deploy-prod.sh").read_text(encoding="utf-8")
        # Gateway's independent retirement check deliberately retains Docker exec.
        serving = source.replace(shell_function("gateway_cutover_gate"), "")
        self.assertNotRegex(serving, r"docker\s+exec[^\n]*\bpython")
        for action in ("begin", "end"):
            self.assertIn(f'api_maintenance -m yleum_api.services.generation_deployment_drain {action} "$SHA"', source)
        self.assertIn("api_maintenance - <<'PY'", source)
        self.assertIn('api_maintenance - "$SHA" <<\'PY\'', source)
        self.assertIn('api_maintenance -c \'import importlib.util;', source)

    def test_current_api_revision_is_pinned_before_old_gate_and_switched_only_after_cutover(self):
        source = (HERE / "deploy-prod.sh").read_text(encoding="utf-8")
        before = shell_function("reader_before")
        self.assertIn('verify-current --release "$API_RUNTIME_SHA"', before)
        self.assertNotIn('verify-current --release "$SHA"', before)
        initial = source.index('API_RUNTIME_SHA=$(api_maintenance --describe-runtime)')
        first_begin = source.index('api_maintenance -m yleum_api.services.generation_deployment_drain begin')
        cutover = source.index("up -d --no-build --force-recreate api worker generation-worker")
        switch = source.index('API_RUNTIME_SHA="$SHA"')
        reachability = source.index("api_maintenance - <<'PY'")
        self.assertLess(initial, first_begin)
        self.assertLess(first_begin, cutover)
        self.assertLess(cutover, switch)
        self.assertLess(switch, reachability)
        # The switch belongs to the full API-only block; web-only keeps CURRENT.
        self.assertIn('if [ $API = 1 ]; then', source[first_begin:cutover])
        self.assertNotIn("\nfi\n", source[cutover:switch])
        self.assertIn('[[ "$API_RUNTIME_SHA" =~ ^[0-9a-f]{40}$ ]]', source[initial:first_begin])


if __name__ == "__main__":
    unittest.main()
