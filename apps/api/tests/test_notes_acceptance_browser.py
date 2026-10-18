"""Real Chromium, DOM actions and a disposable server; no model/provider calls."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest

from tests.behavior_browser_fixture import installed_chromium
from yleum_api.services.generation.notes_acceptance import CHECKS
from yleum_api.services.notes_acceptance_browser import observe_notes, runner_digest

SCRIPT = b"""
const title=document.querySelector('[data-testid=notes-title]');
const body=document.querySelector('[data-testid=notes-body]');let editing=null;
async function render(){
 const rows=await (await fetch('/api/notes')).json();
 const list=document.querySelector('#list');list.replaceChildren();
 for(const n of rows){const row=document.createElement('article');
 row.dataset.testid='notes-row';row.dataset.noteId=n.id;
 for(const v of [n.title,n.body]){const p=document.createElement('p');
 p.textContent=v;row.append(p);}
 const edit=document.createElement('button');edit.dataset.testid='notes-edit';
 edit.textContent='Edit';
 edit.onclick=()=>{editing=n.id;title.value=n.title;body.value=n.body;};row.append(edit);
 const del=document.createElement('button');del.dataset.testid='notes-delete';
 del.textContent='Delete';
 del.onclick=async()=>{await fetch('/api/notes/'+n.id,{method:'DELETE'});await render();};
 row.append(del);
 list.append(row);}
}
document.querySelector('[data-testid=notes-save]').onclick=async()=>{
 await fetch('/api/notes'+(editing?'/'+editing:''),{method:editing?'PATCH':'POST',
 headers:{'Content-Type':'application/json'},body:JSON.stringify({title:title.value,body:body.value})});
 editing=null;title.value='';body.value='';await render();};render();
"""
HTML = b"""<!doctype html><html><body><label>Title<input data-testid="notes-title"></label>
<label>Text<textarea data-testid="notes-body"></textarea></label>
<button data-testid="notes-save">Save</button><section id="list"></section>
<script src="/_next/static/chunks/notes.js"></script></body></html>"""


@pytest.fixture
def notes_server(tmp_path):
    db = sqlite3.connect(tmp_path / "owned-notes.sqlite", check_same_thread=False)
    db.execute("create table notes(id text primary key,title text,body text)")
    existing = (str(uuid4()), "Существующая заметка", "Неприкосновенные данные")
    db.execute("insert into notes values(?,?,?)", existing)
    db.commit()
    state = {"fault": None, "writes": [], "html_reads": 0, "sessions": 0}
    state.update(accepted=threading.Event(), release=threading.Event(), committed=threading.Event())
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send(self, value, content="application/json"):
            raw = (
                value
                if isinstance(value, bytes)
                else json.dumps(value, ensure_ascii=False).encode()
            )
            self.send_response(200)
            self.send_header("Content-Type", content)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except BrokenPipeError:
                pass

        def do_GET(self):
            with lock:
                if self.path == "/bootstrap":
                    state["sessions"] += 1
                    self.send_response(200)
                    self.send_header("Set-Cookie", "owned_fixture=1; Path=/; HttpOnly")
                    self.end_headers()
                    return
                if self.headers.get("Cookie") != "owned_fixture=1":
                    self.send_error(403)
                    return
                if self.path == "/":
                    state["html_reads"] += 1
                    if state["fault"] == "reload_loss" and state["html_reads"] > 1:
                        db.execute("delete from notes where id != ?", (existing[0],))
                        db.commit()
                    return self.send(HTML, "text/html; charset=utf-8")
                if self.path == "/_next/static/chunks/notes.js":
                    script = SCRIPT
                    if state["fault"] == "stale_compilation":
                        script += b"// changed served source"
                    return self.send(script, "text/javascript")
                if self.path == "/api/notes":
                    rows = [
                        dict(zip(("id", "title", "body"), row, strict=True))
                        for row in db.execute("select id,title,body from notes order by id")
                    ]
                    if state["fault"] == "new_context_loss" and state["sessions"] > 1:
                        rows = [row for row in rows if row["id"] == existing[0]]
                    return self.send(rows)
                self.send_error(404)

        def mutate(self, method):
            with lock:
                if self.headers.get("Cookie") != "owned_fixture=1":
                    self.send_error(403)
                    return
                payload = (
                    json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                    if method != "DELETE"
                    else None
                )
                state["writes"].append(method)
                if method == "POST" and state["fault"] == "delayed_commit":
                    state["accepted"].set()
                    if not state["release"].wait(12):
                        return
                identifier = str(uuid4()) if method == "POST" else self.path.rsplit("/", 1)[1]
                if method == "POST":
                    if state["fault"] != "no_persistence":
                        db.execute(
                            "insert into notes values(?,?,?)",
                            (identifier, payload["title"], payload["body"]),
                        )
                elif method == "PATCH" and state["fault"] != "update_noop":
                    db.execute(
                        "update notes set title=?,body=? where id=?",
                        (payload["title"], payload["body"], identifier),
                    )
                elif method == "DELETE" and state["fault"] != "delete_noop":
                    db.execute("delete from notes where id=?", (identifier,))
                db.commit()
                state["committed"].set()
                self.send(
                    {"id": identifier, **payload} if payload is not None else {"deleted": True}
                )

        def do_POST(self):
            self.mutate("POST")

        def do_PATCH(self):
            self.mutate("PATCH")

        def do_DELETE(self):
            self.mutate("DELETE")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    payload = {
        "origin": origin,
        "bootstrap": origin + "/bootstrap",
        "operation_id": str(uuid4()),
        "assets": [
            {
                "path": "/_next/static/chunks/notes.js",
                "sha256": hashlib.sha256(SCRIPT).hexdigest(),
                "bytes": len(SCRIPT),
            }
        ],
        "platform_assets": [],
        "executable_path": installed_chromium(),
        "launch_args": [],
        "timeout_seconds": 35,
        "runner_sha256": runner_digest(),
    }
    yield payload, state, db, existing
    state["release"].set()
    server.shutdown()
    thread.join(timeout=3)
    server.server_close()
    db.close()


def test_real_notes_dom_crud_reload_new_context_and_existing_rows(notes_server):
    payload, state, db, existing = notes_server
    assert observe_notes(payload) == dict.fromkeys(CHECKS, True)
    assert state["writes"] == ["POST", "PATCH", "DELETE"]
    assert state["sessions"] == 3
    assert db.execute("select * from notes").fetchall() == [existing]


@pytest.mark.parametrize(
    "fault",
    [
        "no_persistence",
        "reload_loss",
        "new_context_loss",
        "update_noop",
        "delete_noop",
        "stale_compilation",
    ],
)
def test_real_browser_never_accepts_missing_action_or_persistence(notes_server, fault):
    payload, state, db, existing = notes_server
    state["fault"] = fault
    with pytest.raises(Exception):
        observe_notes(payload)
    assert state["writes"].count("POST") <= 1
    assert db.execute("select * from notes where id=?", (existing[0],)).fetchone() == existing


@pytest.mark.skipif(sys.platform != "linux", reason="Owned guardian uses Linux pidfds/subreaper")
@pytest.mark.parametrize("cancel", [False, True])
def test_actual_notes_guardian_reaps_on_invalid_input_and_cancel(monkeypatch, cancel):
    from yleum_api.services import notes_acceptance_browser as module
    from yleum_api.services.generation.recovery_contract import RecoveryContractError

    processes = []
    original = module._NotesProcess
    ready = None

    class Process(original):
        def __init__(self):
            super().__init__()
            processes.append(self)
            if cancel:
                original_write = self.stdin.write

                async def pending(data):
                    assert ready is not None
                    ready.set()
                    await asyncio.Future()
                    await original_write(data)

                self.stdin.write = pending

    monkeypatch.setattr(module, "_NotesProcess", Process)

    async def exercise():
        nonlocal ready
        ready = asyncio.Event()
        task = asyncio.create_task(module._owned_observation({"timeout_seconds": 2}))
        if cancel:
            await asyncio.wait_for(ready.wait(), timeout=4)
            task.cancel()
        with pytest.raises((RecoveryContractError, asyncio.CancelledError)):
            await task

    asyncio.run(exercise())
    assert len(processes) == 1 and processes[0].returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(processes[0].pid, 0)


def test_reaped_browser_does_not_prove_accepted_server_post_finished(notes_server):
    payload, state, db, existing = notes_server
    state["fault"] = "delayed_commit"
    try:
        with pytest.raises(Exception):
            observe_notes(payload)
        assert state["accepted"].is_set() and not state["committed"].is_set()
        assert state["writes"] == ["POST"]
        # The HTTP handler outlives the closed browser. No retry or cleanup can
        # infer this accepted write's outcome from local process disappearance.
        state["release"].set()
        assert state["committed"].wait(3)
        assert db.execute("select count(*) from notes").fetchone()[0] == 2
        assert db.execute("select * from notes where id=?", (existing[0],)).fetchone() == existing
    finally:
        state["release"].set()
