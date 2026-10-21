"""Cold stream framing with real, inert child processes and private local files."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest
from scripts import backup_cells as cells

NONCE = "0123456789abcdef0123456789abcdef"
BODY = b"payload-without-a-full-read\x00\xff\n"
CHILD = r"""
import hashlib, os, sys
mode, nonce, receipt = sys.argv[1:]
body = b'payload-without-a-full-read\x00\xff\n'
if mode == 'body_magic':
    body += b'YLEUM-COLD-1 ' + b'f' * 32 + b' ' + b'a' * 64 + b'\ncontinued-body'
digest = hashlib.sha256(body).hexdigest()
frame = ('YLEUM-COLD-1 ' + nonce + ' ' + digest + '\n').encode()
if mode == 'corrupt':
    body = b'corrupt' + body
if mode == 'wrong_nonce':
    frame = frame.replace(nonce.encode(), b'f' * 32)
if mode == 'short':
    frame = frame[:-1]
if mode == 'missing':
    frame = b''
if mode.startswith('cut-'):
    frame = frame[:int(mode.split('-')[1])]
if mode == 'duplicate':
    frame += frame
if mode == 'timeout':
    os.write(1, body)
    os.read(0, 1)
    sys.exit(0)
os.write(1, body + frame)
if mode in ('short', 'missing', 'wrong_nonce', 'duplicate') or mode.startswith('cut-'):
    sys.exit(0)
ack = sys.stdin.buffer.readline()
with open(receipt, 'wb') as output:
    output.write(ack)
if ack != ('YLEUM-COLD-ACK-1 ' + nonce + ' ' + digest + '\n').encode():
    sys.exit(7)
if mode == 'extra':
    os.write(1, b'late output')
sys.exit(9 if mode == 'nonzero' else 0)
"""


def transfer(tmp_path, mode="ok", *, timeout=3):
    runner = cells.DockerRunner("python")
    runner.binary = sys.executable
    path, receipt = tmp_path / "payload.tar", tmp_path / "ack.bin"
    result = runner.run_acknowledged_to_file(
        ["-I", "-c", CHILD, mode, NONCE, str(receipt)],
        path=path,
        timeout=timeout,
        nonce=NONCE,
    )
    return result, path, receipt


def test_final_short_read_is_verified_and_acknowledged_before_eof(tmp_path):
    result, path, receipt = transfer(tmp_path)
    assert result.ok
    assert path.read_bytes() == BODY
    assert result.size_bytes == len(BODY)
    assert result.sha256 == hashlib.sha256(BODY).hexdigest()
    assert receipt.read_bytes() == (
        f"YLEUM-COLD-ACK-1 {NONCE} {hashlib.sha256(BODY).hexdigest()}\n".encode()
    )


@pytest.mark.parametrize("mode", ["short", "missing", "wrong_nonce", "corrupt", "duplicate"])
def test_incomplete_or_corrupt_stream_never_receives_ack(tmp_path, mode):
    result, path, receipt = transfer(tmp_path, mode)
    assert not result.ok
    assert path.exists(), "partial bytes remain available for private diagnosis"
    assert not receipt.exists() or receipt.read_bytes() == b""


@pytest.mark.parametrize("mode", ["nonzero", "extra"])
def test_ack_alone_does_not_accept_nonzero_or_late_stdout(tmp_path, mode):
    result, _, receipt = transfer(tmp_path, mode)
    assert not result.ok
    assert receipt.read_bytes()


def test_fsync_failure_closes_child_without_ack(tmp_path, monkeypatch):
    def fail(_fd):
        raise OSError("fixture disk failure")

    monkeypatch.setattr(cells.os, "fsync", fail)
    result, _, receipt = transfer(tmp_path)
    assert not result.ok
    assert not receipt.exists() or receipt.read_bytes() == b""


def test_frame_can_cross_every_read_boundary(tmp_path, monkeypatch):
    original = os.read
    monkeypatch.setattr(cells.os, "read", lambda fd, count: original(fd, min(count, 1)))
    result, path, _ = transfer(tmp_path)
    assert result.ok and path.read_bytes() == BODY


@pytest.mark.parametrize("cut", range(111))
def test_every_truncated_frame_byte_refuses_ack(tmp_path, cut):
    # The literal protocol frame is 111 bytes, including its terminating LF.
    result, _, receipt = transfer(tmp_path, f"cut-{cut}")
    assert not result.ok
    assert not receipt.exists() or receipt.read_bytes() == b""


def test_watchdog_exit_race_does_not_raise_or_change_a_completed_result(tmp_path, monkeypatch):
    original = cells.subprocess.Popen

    def popen(*args, **kwargs):
        process = original(*args, **kwargs)
        kill = process.kill

        def late_kill():
            if process.poll() is not None:
                raise ProcessLookupError("fixture child exited before watchdog kill")
            kill()

        process.kill = late_kill
        return process

    class Timer:
        def __init__(self, _timeout, callback):
            self.callback = callback

        def start(self):
            pass

        def cancel(self):
            self.callback()

    monkeypatch.setattr(cells.subprocess, "Popen", popen)
    monkeypatch.setattr(cells.threading, "Timer", Timer)
    assert transfer(tmp_path)[0].ok


def test_payload_magic_is_retained_as_payload(tmp_path):
    result, path, _ = transfer(tmp_path, "body_magic")
    assert result.ok
    assert path.read_bytes().endswith(b"\ncontinued-body")
    assert result.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_incomplete_producer_is_killed_at_deadline_without_ack(tmp_path):
    result, _, receipt = transfer(tmp_path, "timeout", timeout=0.3)
    assert result.exit_code == 124
    assert not receipt.exists()


def test_fsync_completes_before_ack_write(tmp_path, monkeypatch):
    original = cells.subprocess.Popen
    fsynced = []
    real_fsync = os.fsync

    def fsync(fd):
        real_fsync(fd)
        fsynced.append(fd)

    class Input:
        def __init__(self, source):
            self.source = source

        def write(self, value):
            assert fsynced
            return self.source.write(value)

        def flush(self):
            self.source.flush()

        def close(self):
            self.source.close()

    def popen(*args, **kwargs):
        process = original(*args, **kwargs)
        process.stdin = Input(process.stdin)
        return process

    monkeypatch.setattr(cells.os, "fsync", fsync)
    monkeypatch.setattr(cells.subprocess, "Popen", popen)
    assert transfer(tmp_path)[0].ok


@pytest.mark.parametrize("fault", ["broken_pipe", "short_write"])
def test_ack_write_failure_never_succeeds(tmp_path, monkeypatch, fault):
    original = cells.subprocess.Popen

    class Input:
        def __init__(self, source):
            self.source = source

        def write(self, value):
            if fault == "broken_pipe":
                raise BrokenPipeError("fixture ACK pipe failure")
            return len(value) - 1

        def close(self):
            self.source.close()

    def popen(*args, **kwargs):
        process = original(*args, **kwargs)
        process.stdin = Input(process.stdin)
        return process

    monkeypatch.setattr(cells.subprocess, "Popen", popen)
    result, _, receipt = transfer(tmp_path)
    assert not result.ok
    assert not receipt.exists() or receipt.read_bytes() == b""


def test_ordinary_stream_keeps_raw_bytes_without_protocol(tmp_path):
    runner = cells.DockerRunner("python")
    runner.binary = sys.executable
    path = tmp_path / "ordinary"
    result = runner.run_to_file(
        ["-I", "-c", "import os; os.write(1, b'ordinary-no-frame')"], path=path, timeout=3
    )
    assert result.ok and path.read_bytes() == b"ordinary-no-frame"


@pytest.mark.skipif(os.name != "posix", reason="real Linux pipefail/fd3 producer fixture")
@pytest.mark.parametrize("fault", ["", "tar", "tee", "sha256sum"])
def test_linux_tar_pipeline_waits_for_ack_and_propagates_each_stage_failure(tmp_path, fault):
    source = Path(__file__).resolve().parents[3] / "infra/backup/cold-retained.py"
    spec = importlib.util.spec_from_file_location("cold_ack_producer", source)
    cold = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cold)
    directory = tmp_path / "source"
    directory.mkdir()
    (directory / "file").write_bytes(BODY)
    prefix = ""
    if fault:
        prefix = f'{fault}() {{ command {fault} "$@"; return 9; }}\n'
    runner = cells.DockerRunner(shutil.which("bash") or "/bin/sh")
    result = runner.run_acknowledged_to_file(
        ["-ec", prefix + cold._TAR_STREAM_SCRIPT, "fixture", str(directory), NONCE],
        path=tmp_path / "cold.tar",
        timeout=3,
        nonce=NONCE,
    )
    assert result.ok is (not fault)
    if result.ok:
        assert cold.filesystem_manifest(tmp_path / "cold.tar")["entries"] > 0
