"""Native CPU tests of deadline, bounded pipes, and child process cleanup."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from yleum_orchestrator.services import k8s_config_observer as obs

PROJECT = "6cd1e70b-55b8-4025-b703-63f6d51745a8"


def _request():
    return dict(
        kubeconfig_path="/controller/kubeconfig",
        context=None,
        namespace="app-6cd1e70b-55b8-4025-b703-63f6d51745a8",
        release_id="release-1",
        expected_digest="a" * 64,
        config_version=8,
    )


def _child(monkeypatch, tmp_path: Path, source: str):
    script = tmp_path / "observer.py"
    script.write_text(source, encoding="utf-8")
    monkeypatch.setattr(obs, "OBSERVER_SCRIPT", script)
    monkeypatch.setattr(obs, "OBSERVATION_TIMEOUT_SECONDS", 0.25)
    processes = []
    real_popen = obs.subprocess.Popen

    def popen(argv, **kwargs):
        assert argv == [sys.executable, "-I", str(script)]
        process = real_popen(argv, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(obs.subprocess, "Popen", popen)
    return processes


def test_subprocess_returns_only_valid_confirmation_and_passes_input_privately(
    monkeypatch, tmp_path
):
    processes = _child(
        monkeypatch,
        tmp_path,
        "import json,sys; r=json.load(sys.stdin); "
        "print(json.dumps({'core_matches':r['config_version']==8,'app_matches':True}))",
    )
    assert obs.observe_in_subprocess(**_request()) == {"core_matches": True, "app_matches": True}
    assert len(processes) == 1
    assert processes[0].returncode == 0
    assert processes[0].stdin.closed and processes[0].stdout.closed


@pytest.mark.parametrize(
    ("version", "digest_matches", "confirmed"),
    [
        (0, True, True),
        (1, True, True),
        (9007199254740991, True, True),
        (0, False, False),
        (True, True, False),
        (False, True, False),
        (-1, True, False),
        (0.0, True, False),
        ("0", True, False),
        (9007199254740992, True, False),
    ],
)
def test_main_version_validation_through_real_subprocess_stdin(
    monkeypatch,
    tmp_path,
    version,
    digest_matches,
    confirmed,
):
    # Replace only cluster I/O: the actual child protocol, version validation,
    # envelope parsing and full-config digest comparison execute unchanged.
    files = {
        "core": b'{"content":[]}',
        "app": json.dumps({"config_version": version, "config": {"content": []}}).encode(),
    }
    source_root = str(Path(obs.__file__).resolve().parents[2])
    processes = _child(
        monkeypatch,
        tmp_path,
        "import sys; from types import SimpleNamespace as N\n"
        f"sys.path.insert(0,{source_root!r})\n"
        "from yleum_orchestrator.services import k8s_config_observer as reader\n"
        "api=N(configuration=object(),close=lambda:None)\n"
        "sys.modules['kubernetes']=N(client=N(CoreV1Api=lambda api:None),"
        "config=N(new_client_from_config=lambda **kwargs:api))\n"
        "reader.KubernetesConfigObserver.read_config_files=lambda self,namespace,release_id: "
        f"{files!r} if namespace=='app-{PROJECT}' and release_id=='release-1' else None\n"
        "reader._main()\n",
    )
    request = _request()
    request["config_version"] = version
    if digest_matches:
        request["expected_digest"] = hashlib.sha256(b'{"content":[]}').hexdigest()
    assert obs.observe_in_subprocess(**request) == {
        "core_matches": confirmed,
        "app_matches": confirmed,
    }
    assert processes[0].returncode == 0
    assert processes[0].stdin.closed and processes[0].stdout.closed


@pytest.mark.parametrize(
    "source",
    [
        "import time; time.sleep(60)",
        "import sys; sys.stdout.buffer.write(b'x' * (10 * 1024 * 1024)); sys.stdout.flush()",
    ],
)
def test_stuck_handshake_or_flood_is_killed_and_reaped_without_observer_thread(
    monkeypatch,
    tmp_path,
    source,
):
    processes = _child(monkeypatch, tmp_path, source)
    started = time.monotonic()
    assert obs.observe_in_subprocess(**_request()) == {"core_matches": False, "app_matches": False}
    assert time.monotonic() - started < 2
    assert processes[0].returncode is not None and processes[0].returncode != 0
    assert processes[0].stdin.closed and processes[0].stdout.closed


@pytest.mark.parametrize(
    "payload",
    [
        "broken",
        "x" * 257,
        '{"core_matches":"true","app_matches":true}',
        '{"core_matches":true,"app_matches":true,"config":"not permitted"}',
    ],
)
def test_corrupt_or_oversized_child_response_never_confirms(monkeypatch, tmp_path, payload):
    _child(monkeypatch, tmp_path, "import sys; sys.stdout.write(" + repr(payload) + ")")
    assert obs.observe_in_subprocess(**_request()) == {"core_matches": False, "app_matches": False}


def test_oversized_input_is_refused_before_child_start(monkeypatch):
    monkeypatch.setattr(obs.subprocess, "Popen", lambda *a, **kw: pytest.fail("oversized input"))
    request = _request()
    request["kubeconfig_path"] = "x" * 4096
    assert obs.observe_in_subprocess(**request) == {"core_matches": False, "app_matches": False}


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (
            {"core": b'{"content":[]}', "app": b'{"config_version":8,"config":{"content":[]}}'},
            {"core_matches": True, "app_matches": True},
        ),
        (
            {"core": b'{"content":[1]}', "app": b'{"config_version":8,"config":{"content":[]}}'},
            {"core_matches": False, "app_matches": True},
        ),
        (
            {"core": b'{"content":[]}', "app": b'{"config_version":7,"config":{"content":[]}}'},
            {"core_matches": True, "app_matches": False},
        ),
        (
            {"core": b'{"content":[]}', "app": b"broken"},
            {"core_matches": True, "app_matches": False},
        ),
        (
            {"core": b'{"content":[]}', "app": b" " * (900 * 1024 + 1)},
            {"core_matches": True, "app_matches": False},
        ),
        ({"core": b'{"content":[]}'}, {"core_matches": True, "app_matches": False}),
        (None, {"core_matches": False, "app_matches": False}),
    ],
)
def test_child_matches_actual_full_core_config_and_versioned_app_file(monkeypatch, files, expected):
    reader = obs.KubernetesConfigObserver(None, None)
    monkeypatch.setattr(reader, "read_config_files", lambda *args: files)
    assert (
        reader.observe("namespace", "release-1", hashlib.sha256(b'{"content":[]}').hexdigest(), 8)
        == expected
    )


def test_standalone_helper_rejects_invalid_request_without_diagnostics():
    process = obs.subprocess.run(
        [sys.executable, "-I", str(obs.OBSERVER_SCRIPT)],
        input=b'{"unexpected":"synthetic-private-data"}',
        stdout=obs.subprocess.PIPE,
        stderr=obs.subprocess.PIPE,
        timeout=5,
        check=True,
    )
    assert json.loads(process.stdout) == {"core_matches": False, "app_matches": False}
    assert process.stderr == b""


def _pod(role: str, *, uid: str | None = None, restart: int = 0):
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=role + "-pod",
            uid=uid or role + "-uid",
            deletion_timestamp=None,
            labels={"omnia.release-id": "release-1"},
        ),
        status=SimpleNamespace(
            phase="Running",
            container_statuses=[
                SimpleNamespace(
                    name=role,
                    ready=True,
                    container_id=role + "-container",
                    restart_count=restart,
                )
            ],
        ),
    )


class CoreReader:
    def __init__(self):
        self.pods = {role: _pod(role) for role in ("app", "core")}
        self.replace_on_read = False

    def list_namespaced_pod(self, namespace, *, label_selector, _request_timeout):
        assert 0 < _request_timeout <= 5
        role = label_selector.rsplit("=", 1)[-1]
        return SimpleNamespace(items=[deepcopy(self.pods[role])])

    def read_namespaced_endpoints(self, role, namespace, *, _request_timeout):
        assert 0 < _request_timeout <= 5
        return SimpleNamespace(
            subsets=[
                SimpleNamespace(
                    addresses=[
                        SimpleNamespace(
                            target_ref=SimpleNamespace(
                                kind="Pod", uid=self.pods[role].metadata.uid
                            ),
                        )
                    ]
                )
            ]
        )


def test_cluster_reader_rejects_serving_identity_change_and_uses_fixed_commands(monkeypatch):
    api = object.__new__(obs.KubernetesConfigObserver)
    api._core = CoreReader()
    calls: list[tuple[str, list[str], float]] = []

    def read(namespace, pod, command, *, container, timeout_seconds):
        calls.append((container, command, timeout_seconds))
        if api._core.replace_on_read:
            api._core.pods[container] = _pod(container, restart=1)
        return b'{"content":[]}'

    monkeypatch.setattr(api, "_exec_config_file", read)
    assert api.read_config_files(f"app-{PROJECT}", "release-1") == {
        "core": b'{"content":[]}',
        "app": b'{"content":[]}',
    }
    assert {call[1][0] for call in calls} == {"/usr/bin/python3", "/usr/local/bin/node"}
    assert all(0 < call[2] <= 5 for call in calls)
    assert all(
        "/run/yleum-config/business.json" in " ".join(command)
        or "/app/omnia-business-config.json" in " ".join(command)
        for _, command, _ in calls
    )
    api._core.replace_on_read = True
    assert api.read_config_files(f"app-{PROJECT}", "release-1") is None


def test_cluster_reader_fails_closed_on_missing_file_and_nonserving_pod(monkeypatch):
    api = object.__new__(obs.KubernetesConfigObserver)
    api._core = CoreReader()
    monkeypatch.setattr(api, "_exec_config_file", lambda *args, **kwargs: None)
    assert api.read_config_files(f"app-{PROJECT}", "release-1") is None
    api._core.pods["app"].status.container_statuses[0].ready = False
    monkeypatch.setattr(
        api, "_exec_config_file", lambda *args, **kwargs: pytest.fail("not a serving pod")
    )
    assert api.read_config_files(f"app-{PROJECT}", "release-1") is None


@pytest.mark.parametrize("case", ["valid", "oversized", "stderr", "failed", "timeout"])
def test_config_stream_enforces_output_budget_and_closes_transport(monkeypatch, case):
    independent_exec = object()
    api_client = SimpleNamespace(configuration=object(), close=lambda: None)

    class Stream:
        returncode = 1 if case == "failed" else 0
        closed = False
        updated = False

        def is_open(self):
            return not self.updated

        def update(self, *, timeout):
            assert 0 < timeout <= 5
            if case == "timeout":
                raise TimeoutError
            self.updated = True

        def read_stdout(self, *, timeout):
            assert timeout == 0
            return "x" * (900 * 1024 + 1) if case == "oversized" else '{"content":[]}'

        def read_stderr(self, *, timeout):
            assert timeout == 0
            return "unexpected error" if case == "stderr" else ""

        def close(self):
            self.closed = True

    transport = Stream()

    def stream(method, pod, namespace, **kwargs):
        assert method is independent_exec
        assert kwargs["command"] == ["/trusted/reader"]
        assert 0 < kwargs["_request_timeout"] <= 5
        assert kwargs["_preload_content"] is False
        return transport

    monkeypatch.setitem(sys.modules, "kubernetes.stream", SimpleNamespace(stream=stream))
    monkeypatch.setitem(
        sys.modules,
        "kubernetes",
        SimpleNamespace(
            client=SimpleNamespace(
                ApiClient=lambda **kwargs: api_client,
                CoreV1Api=lambda client: SimpleNamespace(
                    connect_get_namespaced_pod_exec=independent_exec
                ),
            )
        ),
    )
    api = object.__new__(obs.KubernetesConfigObserver)
    api._configuration = api_client.configuration
    api._core = SimpleNamespace(connect_get_namespaced_pod_exec=object())
    result = api._exec_config_file(
        "namespace",
        "pod",
        ["/trusted/reader"],
        container="app",
        timeout_seconds=5,
    )
    assert result == (b'{"content":[]}' if case == "valid" else None)
    assert transport.closed
