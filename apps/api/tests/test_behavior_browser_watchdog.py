from __future__ import annotations

import asyncio
import hashlib
import os
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from yleum_api.services import max_behavior_browser as browser
from yleum_api.services import max_behavior_proof as b

from .behavior_browser_fixture import installed_chromium
from .test_behavior_browser_fixture import registry as registry
from .test_max_behavior_browser import adapter, fixture_files
from .test_max_behavior_browser import local_fixture as local_fixture
from .test_max_behavior_proof import binding, contract


def watched(server, mutation, *, seconds=3):
    server.pending_evaluator = threading.Event()
    marker = "/api/fixture/evaluator-pending"

    class PendingFiles(dict):
        def get(self, path, default=None):
            if path == marker:
                server.pending_evaluator.set()
                return b"{}"
            return super().get(path, default)

    server.files = PendingFiles(fixture_files(""))
    javascript = server.files["/_next/static/chunks/a.js"]
    # A synchronous fixture-only read proves the evaluator reached the pending
    # operation. Append after initialization: startup getItem calls are not proof.
    signal = (
        b"const signal=new XMLHttpRequest();"
        b"signal.open('GET','/api/fixture/evaluator-pending',false);signal.send();"
    )
    if mutation == "storage":
        javascript += (
            b"\nStorage.prototype.getItem=()=>{" + signal + b"return new Promise(()=>{});};"
        )
    elif mutation == "paint":
        javascript += b"\nElement.prototype.checkVisibility=()=>{" + signal + b"while(true){}};"
    server.files["/_next/static/chunks/a.js"] = javascript
    bound = binding()
    witness = b.CompiledAssetWitness(
        bound,
        tuple(
            b.ObservedAsset(path, hashlib.sha256(body).hexdigest(), len(body))
            for path, body in server.files.items()
            if path != "/"
        ),
        "f" * 64,
        "served_candidate_compilation_v1",
    )
    endings = []

    async def resolver(request):
        return witness

    registered = browser.make_private_browser_driver(
        executable_path=installed_chromium(),
        adapter=replace(adapter(), read_paths=(marker,)),
        launch_args=("--no-sandbox",),
        resolve_candidate_compilation=resolver,
        worker_timeout_seconds=seconds,
        on_worker_closed=lambda *record: endings.append(record),
    )
    request = b.BehaviorDriverInput(bound, contract(), b.PrivatePreviewCapability(NS()))
    return registered, request, witness, endings


def assert_owned_processes_gone(endings):
    assert endings
    for guardian, group, _closed in endings:
        assert not Path(f"/proc/{guardian}").exists()
        if group:
            with pytest.raises(ProcessLookupError):
                os.killpg(group, 0)


@pytest.mark.parametrize("pending", ["storage", "paint"])
def test_actual_pending_generated_evaluator_deadline_kills_and_reaps(local_fixture, pending):
    # The worker budget includes real Chromium startup, navigation and measured
    # controls. It must allow entry before testing a pending evaluator's deadline.
    registered, request, witness, endings = watched(local_fixture, pending, seconds=10)
    started = time.monotonic()
    with pytest.raises(b.BehaviorProofError, match="BEHAVIOR_BROWSER_DEADLINE"):
        asyncio.run(registered.observe_browser(request, witness))
    assert time.monotonic() - started < 15
    assert local_fixture.pending_evaluator.is_set()
    assert endings[0][2] is True
    assert local_fixture.reads >= 3 and local_fixture.mutations == 0
    assert endings[0][2] is True
    assert_owned_processes_gone(endings)


def test_actual_pending_evaluator_cancellation_waits_for_owned_cleanup(local_fixture):
    registered, request, witness, endings = watched(local_fixture, "storage", seconds=35)

    async def cancel():
        task = asyncio.create_task(registered.observe_browser(request, witness))
        try:
            assert await asyncio.to_thread(local_fixture.pending_evaluator.wait, 20)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(cancel())
    assert local_fixture.reads >= 3 and local_fixture.mutations == 0
    assert endings[0][2] is True
    assert_owned_processes_gone(endings)


def test_cancellation_resistant_startup_cannot_dispatch_or_orphan(local_fixture, monkeypatch):
    registered, request, witness, endings = watched(local_fixture, "storage", seconds=35)
    processes = []
    original_owned = browser._OwnedProcess
    writes = []
    original_write = browser._PrivatePipe.write

    def acquire(environment):
        owned = original_owned(environment)
        processes.append(owned)
        return owned

    async def record_write(pipe, payload):
        writes.append(True)
        await original_write(pipe, payload)

    async def exercise():
        started = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def resistant_readline(_pipe):
            started.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass  # simulate transport setup ignoring cancellation
            finished.set()
            return f'{{"guardian":{processes[0].pid},"ready":true}}\n'.encode()

        monkeypatch.setattr(browser, "_OwnedProcess", acquire)
        monkeypatch.setattr(browser._PrivatePipe, "readline", resistant_readline)
        monkeypatch.setattr(browser._PrivatePipe, "write", record_write)
        task = asyncio.create_task(registered.observe_browser(request, witness))
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        try:
            with pytest.raises(b.BehaviorProofError, match="BEHAVIOR_BROWSER_CLEANUP_FAILED"):
                await asyncio.wait_for(task, 6)
            assert processes[0].returncode is not None
            assert_owned_processes_gone(endings)
            assert writes == []  # no private payload or browser after cancellation
        finally:
            release.set()
            await asyncio.wait_for(finished.wait(), 1)
        assert len(processes) == 1 and processes[0].returncode is not None

    asyncio.run(exercise())
    assert local_fixture.reads == local_fixture.mutations == 0
    assert endings[0][1:] == (0, False)  # explicitly unconfirmed, never PASS


def test_actual_guardian_pre_payload_stdin_has_its_own_deadline():
    import subprocess
    import sys

    owned = subprocess.Popen(
        [sys.executable, "-m", "yleum_api.services.max_behavior_browser_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        assert owned.wait(timeout=7) != 0
        assert not Path(f"/proc/{owned.pid}").exists()
    finally:
        if owned.poll() is None:
            owned.kill()
            owned.wait(timeout=1)
        owned.stdin.close()
        owned.stdout.close()


@pytest.mark.parametrize("stuck", ["postkill-reap", "final-pipe"])
def test_unconfirmed_fake_transport_cleanup_is_bounded_and_never_pass(
    stuck, monkeypatch, registry
):
    executable_path = installed_chromium()

    class Pipe:
        closing = False

        async def write(self, _payload):
            pass

        def close(self):
            self.closing = True

        def is_closing(self):
            return self.closing

    async def exercise():
        pending = asyncio.Event()
        count = 0
        process = NS(pid=4000001, returncode=None, stdin=Pipe())

        async def readline():
            nonlocal count
            count += 1
            if count == 1:
                return b'{"guardian":4000001,"ready":true}\n'
            if count == 2:
                return b'{"guardian":4000001,"owned_group":4000002}\n'
            pending.set()
            await asyncio.Future()

        async def wait():
            if stuck == "postkill-reap":
                await asyncio.Future()
            process.returncode = 0
            return 0

        async def read(_limit):
            await asyncio.Future()

        def acquire(_environment):
            return process

        process.stdout = NS(readline=readline, read=read)
        process.wait = wait
        process.send_signal = lambda _signal: None
        process.close_pipes = lambda: None
        killed = []
        monkeypatch.setattr(browser, "_OwnedProcess", acquire)
        monkeypatch.setattr(os, "killpg", lambda group, sig: killed.append((group, sig)))
        monkeypatch.setattr(browser, "_origin", lambda _: ("http://fixture", "http://fixture/b"))
        bound = binding()
        witness = b.CompiledAssetWitness(
            bound,
            (b.ObservedAsset("/_next/static/chunks/a.js", "a" * 64, 1),),
            "f" * 64,
            "served_candidate_compilation_v1",
        )
        request = b.BehaviorDriverInput(bound, contract(), b.PrivatePreviewCapability(NS()))
        endings = []
        task = asyncio.create_task(
            browser._execute_owned_process(
                request,
                witness,
                True,
                executable_path=executable_path,
                launch_args=(),
                adapter=adapter(),
                timeout_seconds=35,
                on_closed=lambda *record: endings.append(record),
            )
        )
        await asyncio.wait_for(pending.wait(), 1)
        started = time.monotonic()
        task.cancel()
        with pytest.raises(
            b.BehaviorProofError, match="BEHAVIOR_BROWSER_CLEANUP_FAILED"
        ) as failure:
            await asyncio.wait_for(task, 6)
        assert failure.value.status == "NEEDS_REVIEW"
        assert time.monotonic() - started < 6
        assert endings == [(4000001, 4000002, False)]
        if stuck == "postkill-reap":
            assert {group for group, _sig in killed} == {4000001, 4000002}

    asyncio.run(exercise())


@pytest.mark.parametrize("writing", [False, True])
def test_canceled_pipe_waiter_cannot_remove_reused_descriptor_reader(writing):
    async def exercise():
        read_fd, write_fd = os.pipe()
        owned_fd = write_fd if writing else read_fd
        other_fd = read_fd if writing else write_fd
        pipe = browser._PrivatePipe(os.fdopen(owned_fd, "wb" if writing else "rb", buffering=0))
        loop = asyncio.get_running_loop()
        waiter = asyncio.create_task(pipe.ready(writing=writing))
        await asyncio.sleep(0)  # registration exists; canceled finally has not run
        waiter.cancel()
        pipe.close()
        fresh_read, fresh_write = os.pipe()
        callback = asyncio.Event()
        try:
            assert fresh_read == owned_fd  # actual kernel descriptor reuse
            loop.add_reader(fresh_read, callback.set)
            with pytest.raises(asyncio.CancelledError):
                await waiter
            os.write(fresh_write, b"witness")
            await asyncio.wait_for(callback.wait(), 1)
            assert os.read(fresh_read, 7) == b"witness"
        finally:
            loop.remove_reader(fresh_read)
            os.close(fresh_read)
            os.close(fresh_write)
            os.close(other_fd)
            pipe.close()  # idempotent; must also leave the recycled FD untouched

    asyncio.run(exercise())
