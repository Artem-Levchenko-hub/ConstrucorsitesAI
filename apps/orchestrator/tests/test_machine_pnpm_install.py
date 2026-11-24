from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests._generation_reader_settings import legacy_reader_process_settings  # noqa: F401
from yleum_orchestrator.services.machine_pnpm_install import (
    FROZEN_INSTALL,
    frozen_install_command,
    install_environment,
)


def test_plain_install_sets_only_the_relay_and_its_no_proxy_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "exists", lambda _: False)
    env = {
        "HOME": str(tmp_path), "HTTPS_PROXY": "http://10.1.2.3:3128",
        "no_proxy": "localhost,127.0.0.1", "NO_PROXY": "localhost,example.org",
        "npm_config_store_dir": "/pnpm/store",
    }
    assert install_environment("10.1.2.3", tmp_path / "work", env) == {
        **env, "npm_config_registry": "http://10.1.2.3:3128/__yleum/npm/",
        "no_proxy": "localhost,127.0.0.1,example.org,10.1.2.3",
        "NO_PROXY": "localhost,127.0.0.1,example.org,10.1.2.3",
    }
    assert "npm_config_registry" not in env


@pytest.mark.parametrize("location", ["project", "workspace", "home", "pnpm-home"])
def test_install_preserves_existing_project_and_account_config(tmp_path, location):
    cwd = tmp_path / "work" / "child"
    home = tmp_path / "home"
    config = {
        "project": cwd / ".npmrc", "workspace": cwd.parent / ".npmrc",
        "home": home / ".npmrc", "pnpm-home": home / ".config/pnpm/rc",
    }[location]
    config.parent.mkdir(parents=True)
    config.write_text("@scope:registry=https://packages.example.test/\n", encoding="utf-8")
    env = {"HOME": str(home)}
    assert install_environment("10.1.2.3", cwd, env) is env


@pytest.mark.parametrize("key", [
    "NPM_CONFIG_REGISTRY", "npm_config_@scope:registry", "npm_config_userconfig",
    "npm_config_globalconfig", "NODE_AUTH_TOKEN", "npm_config__auth", "DESTDIR",
])
def test_install_preserves_custom_registry_or_auth_environment(tmp_path, key):
    env = {key: "test-value"}
    assert install_environment("10.1.2.3", tmp_path, env) is env


@pytest.mark.parametrize("key", ["PREFIX", "npm_config_prefix", "NPM_CONFIG_PREFIX"])
def test_prefix_only_bypasses_relay_when_it_has_a_global_config(tmp_path, key, monkeypatch):
    prefix = tmp_path / "custom"
    config = prefix / "etc/npmrc"
    env = {key: str(prefix)}
    monkeypatch.setattr(Path, "exists", lambda _path: False)
    assert "npm_config_registry" in install_environment("10.1.2.3", tmp_path, env)
    monkeypatch.setattr(Path, "exists", lambda path: path == config)
    assert install_environment("10.1.2.3", tmp_path, env) is env


@pytest.mark.parametrize(("command", "proxy_ip", "wrapped"), [
    (FROZEN_INSTALL, "172.20.0.3", True),
    (FROZEN_INSTALL, None, False),
    (FROZEN_INSTALL, "8.8.8.8", False),
    (FROZEN_INSTALL, "127.0.0.1", False),
    (FROZEN_INSTALL, "10.1.2.3/anything", False),
    (["pnpm", "install"], "172.20.0.3", False),
    (["pnpm", "install", "--frozen-lockfile", "--offline"], "172.20.0.3", False),
    (["pnpm", "test"], "172.20.0.3", False),
])
def test_existing_machine_exec_scopes_relay_to_frozen_install(command, proxy_ip, wrapped):
    guest = frozen_install_command(command, proxy_ip)
    if wrapped:
        assert guest[:4] == ["python3", "-I", "-S", "-c"]
        assert "os.execvpe" in guest[4]
        assert guest[-1] == proxy_ip
    else:
        assert guest == command


@pytest.mark.parametrize("published", [False, True])
def test_only_development_proxy_mounts_the_accepted_seed_and_rotates_on_change(
    tmp_path, monkeypatch, published,
):
    pytest.importorskip("fcntl", reason="the complete Cell backend runs on Linux")
    from tests.test_docker_machine_backend import backend
    from tests.test_published_machine_backend import published_backend
    from yleum_orchestrator.services import docker_machine_backend as machine

    runtime = published_backend(tmp_path) if published else backend(tmp_path)
    seed = Mock(side_effect=[None, tmp_path / "revision-a", tmp_path / "revision-b"])
    monkeypatch.setattr(machine, "npm_seed_directory", seed)
    monkeypatch.setattr(runtime, "_network", lambda *_a, **_kw: SimpleNamespace(name="egress"))
    monkeypatch.setattr(runtime, "_wait_proxy_ready", lambda *_: None)
    proxy_ip = "172.20.0.3"
    current = None
    created = []

    def create(_image, command, **opts):
        nonlocal current
        current = SimpleNamespace(
            status="running", labels=opts["labels"], start=Mock(), reload=Mock(), remove=Mock(),
            attrs={"NetworkSettings": {"Networks": {runtime.internal_network: {
                "IPAddress": proxy_ip,
            }}}},
        )
        created.append((command, opts, current))
        return current

    runtime.client = SimpleNamespace(containers=SimpleNamespace(create=create))
    monkeypatch.setattr(runtime, "_lookup", lambda *args: current)
    internal = SimpleNamespace(connect=Mock())
    for _ in range(3):
        assert runtime._ensure_egress_proxy(internal) == proxy_ip
    assert "--npm-tarball-cache" not in created[0][0]
    assert created[0][1]["volumes"] == {}
    if published:
        seed.assert_not_called()
        assert len(created) == 1
        return
    assert len(created) == 3
    assert internal.connect.call_args.kwargs["ipv4_address"] == proxy_ip
    for (command, options, _), revision in zip(created[1:], ("revision-a", "revision-b"),
                                               strict=True):
        assert command[command.index("--npm-tarball-cache") + 1] == "/opt/yleum/npm-tarballs"
        assert options["volumes"] == {
            str(tmp_path / revision): {"bind": "/opt/yleum/npm-tarballs", "mode": "ro"},
        }
    for _, _, stale in created[:2]:
        stale.remove.assert_called_once_with(force=True)
