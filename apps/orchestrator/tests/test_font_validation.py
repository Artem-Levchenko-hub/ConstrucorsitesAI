import json
import socket
from types import SimpleNamespace as NS

import pytest

from yleum_orchestrator.core.errors import OrchestratorError
from yleum_orchestrator.services import font_validation as fonts


def frame(raw, stream=1):
    return bytes([stream, 0, 0, 0]) + len(raw).to_bytes(4, "big") + raw


def setup(monkeypatch, output=None, exit_code=0, error=None, env=None):
    class Channel:
        def __init__(self):
            self.chunks = [frame(json.dumps(output or {"family": "Fixture"}).encode()), b""]
            self.sent = []

        def settimeout(self, value):
            assert 0 < value <= 20

        def sendall(self, data):
            self.sent.append(data)

        def shutdown(self, how):
            assert how == socket.SHUT_WR

        def recv(self, _size):
            if error:
                raise error
            return self.chunks.pop(0)

    channel = Channel()
    connection = NS(_sock=channel, close=lambda: None)
    removed, created = [], []
    helper = NS(
        id="c" * 64,
        attach_socket=lambda **kw: connection,
        start=lambda: None,
        wait=lambda **kw: {"StatusCode": exit_code},
        remove=lambda **kw: removed.append(kw),
    )
    image = NS(id="sha256:" + "a" * 64, attrs={"Config": {"Env": env or ["PATH=/bin"]}})
    client = NS(
        images=NS(get=lambda tag: image),
        containers=NS(
            create=lambda *args, **kw: created.append((args, kw)) or helper, list=lambda **kw: []
        ),
        close=lambda: None,
    )
    monkeypatch.setattr(fonts.docker, "DockerClient", lambda **kw: client)
    monkeypatch.setattr(
        fonts,
        "get_settings",
        lambda: NS(
            omnia_release_sha="b" * 40,
            docker_host="unix:///var/run/docker.sock",
        ),
    )
    return channel, created, removed


def test_job_is_fixed_networkless_and_has_no_reader_or_project_authority(monkeypatch):
    channel, created, removed = setup(monkeypatch)
    assert fonts.run_font_validation(b"font")["family"] == "Fixture"
    args, options = created[0]
    assert args[0] == "sha256:" + "a" * 64
    assert args[1][-2:] == ["-I", "/app/src/yleum_api/services/font_validation_worker.py"]
    assert options["entrypoint"] == []
    assert options["network_mode"] == "none" and options["read_only"]
    assert options["cap_drop"] == ["ALL"] and not options["privileged"]
    assert options["security_opt"] == ["no-new-privileges:true"]
    assert options["user"] == "10001:10001"
    assert options["pids_limit"] == 16
    assert options["mem_limit"] == options["memswap_limit"] == 512 * 1024**2
    assert options["nano_cpus"] == 1_000_000_000
    assert not options.get("volumes") and not options.get("ports")
    assert not options["use_config_proxy"]
    assert options["environment"] == {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/tmp"}
    assert channel.sent == [b"font"] and removed == [{"force": True}]


@pytest.mark.parametrize("raw", [b"", b"x" * (8 * 1024**2 + 1)], ids=["empty", "oversized"])
def test_rejects_input_before_creating_job(monkeypatch, raw):
    _, created, removed = setup(monkeypatch)
    with pytest.raises(OrchestratorError) as exc:
        fonts.run_font_validation(raw)
    assert exc.value.status_code == 422
    assert not created and not removed


@pytest.mark.parametrize("error", [TimeoutError(), RuntimeError("private diagnostic")])
def test_transport_failure_removes_exact_job_without_leaking_message(monkeypatch, error):
    _, _, removed = setup(monkeypatch, error=error)
    with pytest.raises(OrchestratorError) as exc:
        fonts.run_font_validation(b"font")
    assert exc.value.status_code == 503 and "private" not in str(exc.value)
    assert removed == [{"force": True}]


def test_nonzero_parser_exit_is_invalid_font_and_cleans_up(monkeypatch):
    _, _, removed = setup(monkeypatch, exit_code=1)
    with pytest.raises(OrchestratorError) as exc:
        fonts.run_font_validation(b"font")
    assert exc.value.status_code == 422 and removed == [{"force": True}]


@pytest.mark.parametrize(
    "chunks",
    [
        [frame(b"x" * (2 * 1024**2 + 1)), b""],
        [frame(b"x" * (1024**2 + 1), 2), frame(b"x" * 1024**2), b""],
        [b"\1\0\0", b""],
    ],
    ids=["stdout_limit", "combined_limit", "truncated_frame"],
)
def test_combined_stdout_stderr_and_truncated_frames_are_bounded(monkeypatch, chunks):
    channel, _, removed = setup(monkeypatch)
    channel.chunks = chunks
    with pytest.raises(OrchestratorError):
        fonts.run_font_validation(b"font")
    assert removed == [{"force": True}]


def test_image_with_embedded_credentials_is_rejected_before_job(monkeypatch):
    _, created, _ = setup(monkeypatch, env=["DATABASE_URL=private"])
    with pytest.raises(OrchestratorError) as exc:
        fonts.run_font_validation(b"font")
    assert exc.value.status_code == 503 and not created


def test_absolute_deadline_also_bounds_continuously_producing_output(monkeypatch):
    channel, _, removed = setup(monkeypatch)
    current = [0.0]

    def recv(_size):
        current[0] += 11
        return frame(b" ", 2)

    channel.recv = recv
    monkeypatch.setattr(fonts, "monotonic", lambda: current[0])
    with pytest.raises(OrchestratorError):
        fonts.run_font_validation(b"font")
    assert removed == [{"force": True}]


def test_job_from_prior_controller_must_be_confirmed_stopped_before_new_job(monkeypatch):
    _, created, _ = setup(monkeypatch)
    client = fonts.docker.DockerClient()
    client.containers.list = lambda **kw: [NS(attrs={"State": {"Status": "created"}})]
    with pytest.raises(OrchestratorError) as exc:
        fonts.run_font_validation(b"font")
    assert exc.value.status_code == 503 and not created
