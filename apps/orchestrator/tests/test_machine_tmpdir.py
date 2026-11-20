"""The generated tool environment uses owned disk, including warm machines."""
import ast
import errno
import io
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

_SOURCE = (
    Path(__file__).resolve().parents[1] / "src/yleum_orchestrator/services"
    / "docker_machine_backend.py"
)
_TREE = ast.parse(_SOURCE.read_text(encoding="utf-8"))
_CONSTANTS = {"_PROCESS_ENVIRONMENT", "_EXEC_WRAPPER", "_SERVICE_WRAPPER"}
_namespace = {}
for _node in _TREE.body:
    if isinstance(_node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id in _CONSTANTS for target in _node.targets
    ):
        exec(compile(ast.Module(body=[_node], type_ignores=[]), str(_SOURCE), "exec"), _namespace)
# These are the exact production wrapper expressions, without importing Docker
# or Linux-only controller dependencies just to test a standard-library script.
module = SimpleNamespace(**{name: _namespace[name] for name in _CONSTANTS})


class DirectoryOS:
    """Portable directory-descriptor double; real POSIX cases are below."""

    O_RDONLY = 0
    O_DIRECTORY = 0o200000
    O_NOFOLLOW = 0o400000

    def __init__(self, environ=None, *, fail=None):
        self.environ = dict(environ or {})
        self.calls = []
        self.opened = set()
        self.fail = fail
        self.next_fd = 10

    def open(self, path, flags, *, dir_fd=None):
        self.calls.append(("open", path, flags, dir_fd))
        assert flags == self.O_RDONLY | self.O_DIRECTORY | self.O_NOFOLLOW
        if path == self.fail:
            raise OSError(errno.ELOOP, "symlink refused")
        if dir_fd is not None:
            assert dir_fd in self.opened
        self.next_fd += 1
        self.opened.add(self.next_fd)
        return self.next_fd

    def mkdir(self, path, *, mode, dir_fd):
        assert mode == 0o700 and dir_fd in self.opened
        self.calls.append(("mkdir", path, mode, dir_fd))

    def close(self, fd):
        assert fd in self.opened
        self.opened.remove(fd)


def environment_function(os_module):
    namespace = {"os": os_module}
    exec(module._PROCESS_ENVIRONMENT, namespace)
    return namespace["process_environment"]


@pytest.mark.parametrize("provided", [{}, {"TMPDIR": ""}, {"TMPDIR": "", "TMP": ""}])
def test_new_and_warm_processes_default_to_project_home_without_tmpfs_change(provided):
    fake = DirectoryOS({"HOME": "/root", "PATH": "/bin", **provided})
    result = environment_function(fake)()
    assert result == {**fake.environ, "TMPDIR": "/root/.cache/yleum/tmp"}
    assert [call[1] for call in fake.calls if call[0] == "mkdir"] == [".cache", "yleum", "tmp"]
    assert not fake.opened
    assert "TMPDIR" not in fake.environ or fake.environ["TMPDIR"] == ""


@pytest.mark.parametrize("provided", [
    {"TMPDIR": "/owned/custom"}, {"TMP": "/owned/tmp"}, {"TEMP": "/owned/temp"},
    {"TMPDIR": "", "TMP": "/owned/tmp"},
    {"TMPDIR": "/owned/first", "TMP": "/owned/second", "TEMP": "/owned/third"},
])
def test_explicit_temporary_environment_and_other_authority_are_preserved(provided):
    original = {"PATH": "/usr/bin", "HOME": "/root", "HTTP_PROXY": "http://proxy:3128",
                "DATABASE_URL": "postgresql://project-only", **provided}
    fake = DirectoryOS(original)
    assert environment_function(fake)() == original
    assert fake.calls == []
    assert fake.environ == original


def test_existing_advertised_default_is_prepared_not_assumed_to_exist():
    fake = DirectoryOS({"TMPDIR": "/root/.cache/yleum/tmp"})
    assert environment_function(fake)()["TMPDIR"] == "/root/.cache/yleum/tmp"
    assert len([call for call in fake.calls if call[0] == "mkdir"]) == 3


@pytest.mark.parametrize("component", ["/root", ".cache", "yleum", "tmp"])
def test_unavailable_or_symlinked_default_fails_closed_and_closes_descriptors(component):
    fake = DirectoryOS(fail=component)
    with pytest.raises(OSError, match="symlink refused"):
        environment_function(fake)()
    assert not fake.opened
    assert "TMPDIR" not in fake.environ


@pytest.mark.parametrize("kind", ["command", "service"])
def test_actual_wrappers_supply_default_to_children_of_existing_machine(tmp_path, kind):
    fake_os = DirectoryOS({"HOME": "/root", "PATH": "/bin", "HTTP_PROXY": "http://proxy"})
    children = []

    def spawn(argv, **kwargs):
        children.append((argv, kwargs))
        return SimpleNamespace(pid=123, stdout=io.BytesIO(b"tool result"), wait=lambda: 0)

    log = str(tmp_path / "command.log")
    args = [["sh", "-c", "TMPDIR=/explicit node script.mjs"], "/workspace", log,
            str(tmp_path / "command.pid") if kind == "command" else "never"]
    script = module._EXEC_WRAPPER if kind == "command" else module._SERVICE_WRAPPER
    # Keep all real wrapper statements and inject only its standard-library
    # collaborators. No Docker, shell or guest program is executed in this test.
    tree = ast.parse(script)
    tree.body = [node for node in tree.body if not isinstance(node, ast.Import)]
    namespace = {"json": json, "os": fake_os,
                 "subprocess": SimpleNamespace(Popen=spawn, PIPE=-1, STDOUT=-2),
                 "sys": SimpleNamespace(argv=["wrapper", json.dumps(args)], exit=sys.exit),
                 "time": SimpleNamespace(sleep=lambda _: None)}
    with pytest.raises(SystemExit) as result:
        exec(compile(tree, "trusted-process-wrapper", "exec"), namespace)
    assert result.value.code == 0 and len(children) == 1
    argv, options = children[0]
    assert argv == args[0]  # An explicit inline TMPDIR stays an ordinary argv.
    assert options["env"] == {**fake_os.environ, "TMPDIR": "/root/.cache/yleum/tmp"}
    assert options["cwd"] == "/workspace"
    assert options.get("start_new_session", False) is (kind == "command")
    assert Path(log).read_bytes() == b"tool result"
    assert not fake_os.opened


@pytest.mark.skipif(os.name != "posix", reason="real openat/no-follow semantics require POSIX")
def test_native_directory_is_private_reusable_and_does_not_delete_cache(tmp_path, monkeypatch):
    for key in ("TMPDIR", "TMP", "TEMP"):
        monkeypatch.delenv(key, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    prepare = environment_function(os)
    first = prepare(str(home))
    target = Path(first["TMPDIR"])
    sentinel = target / "keep-existing-data"
    sentinel.write_bytes(b"retained")
    second = prepare(str(home))
    assert first == second and sentinel.read_bytes() == b"retained"
    assert target == home / ".cache/yleum/tmp"
    assert stat.S_IMODE(target.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name != "posix", reason="real openat/no-follow semantics require POSIX")
@pytest.mark.parametrize("component", ["home", ".cache", "yleum", "tmp"])
def test_native_symlink_never_reaches_outside_directory(tmp_path, monkeypatch, component):
    for key in ("TMPDIR", "TMP", "TEMP"):
        monkeypatch.delenv(key, raising=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    components = [home, home / ".cache", home / ".cache/yleum", home / ".cache/yleum/tmp"]
    selected = components[["home", ".cache", "yleum", "tmp"].index(component)]
    selected.parent.mkdir(parents=True, exist_ok=True)
    if selected == home:
        home.rmdir()
    selected.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        environment_function(os)(str(home))
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="actual disk-backed chunk writes require POSIX")
def test_native_disk_temp_roundtrip_exceeds_old_128_mib_limit(tmp_path, monkeypatch):
    for key in ("TMPDIR", "TMP", "TEMP"):
        monkeypatch.delenv(key, raising=False)
    configured_home = os.environ.get("OMNIA_TMPDIR_NATIVE_HOME")
    home = Path(configured_home) if configured_home else tmp_path / "home"
    if not configured_home:
        home.mkdir()
    env = environment_function(os)(str(home))
    chunk = b"project disk temp verification\n" * 32768
    written = 0
    target_bytes = 160 * 1024**2
    with tempfile.TemporaryFile(dir=env["TMPDIR"]) as output:
        while written < target_bytes:
            data = chunk[:min(len(chunk), target_bytes - written)]
            assert output.write(data) == len(data)
            written += len(data)
        output.flush()
        os.fsync(output.fileno())
        assert os.fstat(output.fileno()).st_size == target_bytes
        output.seek(0)
        read = 0
        while data := output.read(len(chunk)):
            assert data == chunk[:len(data)]
            read += len(data)
        assert read == target_bytes
    if os.environ.get("OMNIA_TMPDIR_ENOSPC_SENTINEL") == "1":
        # Root's isolated Docker fixture gives only its own /tmp a 128 MiB
        # tmpfs. Writing actual pages there must reproduce the former limit.
        assert os.statvfs("/tmp").f_blocks * os.statvfs("/tmp").f_frsize <= 128 * 1024**2
        with (
            tempfile.TemporaryFile(dir="/tmp", buffering=0) as old,
            pytest.raises(OSError) as failure,
        ):
            remaining = target_bytes
            while remaining:
                data = chunk[:min(len(chunk), remaining)]
                remaining -= old.write(data)
        assert failure.value.errno == errno.ENOSPC
