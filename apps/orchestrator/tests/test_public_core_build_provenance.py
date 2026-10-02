"""Execute the real builder against explicit synthetic Docker metadata.

These are provenance refusal controls, not fake image/native/runtime acceptance.
Actual image builds and PG/HTTP checks are separate integration evidence.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts/build-public-max-core.sh"
KIT = "sha256:" + "a" * 64
NODE = "sha256:" + "b" * 64
OUTPUT = "omnia-max-public-core:qa-provenance"
KIT_ALIAS = "omnia-max-core-build-base:" + "a" * 64
NODE_ALIAS = "omnia-max-core-runtime-base:" + "b" * 64

DOCKER = r'''
import json, os, sys
from pathlib import Path
p=Path(os.environ["QA_DOCKER_STATE"]); state=json.loads(p.read_text()); args=sys.argv[1:]
state["calls"].append(args)
try:
 if args[:2]==["image","inspect"]:
  image=state["images"].get(args[2])
  if image is None: sys.exit(1)
  fmt=args[args.index("--format")+1] if "--format" in args else ""
  if fmt=="{{.Id}}": print(image["id"])
  elif "Config.Labels" in fmt: print("1")
  else: print(json.dumps(image))
 elif args[:2]==["image","tag"]:
  state["images"][args[3]]=dict(state["images"][args[2]])
 elif args[0]=="build":
  state["images"][args[args.index("-t")+1]]={"id":"sha256:"+"c"*64}
  if state.get("drift"):
   state["images"][state["drift"]]["id"]="sha256:"+"d"*64
 elif args[0]=="run":
  if args[-2]==state.get("node_id") and not state.get("has_node",True): sys.exit(127)
  print(state.get("runtime_version","v22.23.3") if args[-2]==state.get("node_id")
        else state.get("kit_version","v22.23.3"))
 else: sys.exit(2)
finally: p.write_text(json.dumps(state))
'''


def invoke(tmp_path, *, change=None, missing_node=False, wrong_alias=False, drift=None,
           runtime_version="v22.23.3", kit_version="v22.23.3", has_node=True):
    node = {"id": NODE, "os": "linux", "arch": "amd64", "layers": ["node-layer-1", "node-layer-2"]}
    if change:
        node.update(change)
    state = {"images": {
        KIT: {"id": KIT, "os": "linux", "arch": "amd64",
              "layers": ["node-layer-1", "node-layer-2", "kit-layer"]},
        NODE: node,
    }, "calls": [], "drift": drift, "node_id": NODE, "has_node": has_node,
        "runtime_version": runtime_version, "kit_version": kit_version}
    if wrong_alias:
        state["images"][NODE_ALIAS] = dict(node, id="sha256:" + "d" * 64)
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(state))
    docker = tmp_path / "docker"
    docker.write_text(f"#!{sys.executable}\n" + DOCKER)
    docker.chmod(0o755)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "QA_DOCKER_STATE": str(state_file)}
    args = ["bash", str(BUILDER), KIT, OUTPUT, *([] if missing_node else [NODE])]
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=15)
    return result, json.loads(state_file.read_text())["calls"]


def test_missing_runtime_base_fails_before_build(tmp_path):
    result, calls = invoke(tmp_path, missing_node=True)
    assert result.returncode != 0
    assert not any(call[0] == "build" for call in calls)


@pytest.mark.parametrize("change", [
    {"id": "sha256:" + "d" * 64},
    {"os": "windows"},
    {"arch": "arm64"},
    {"layers": []},
    {"layers": ["other-layer"]},
    {"layers": ["node-layer-2", "node-layer-1"]},
    {"layers": ["node-layer-1", "node-layer-2", "not-kit-ancestor"]},
])
def test_wrong_runtime_provenance_fails_before_build(tmp_path, change):
    result, calls = invoke(tmp_path, change=change)
    assert result.returncode != 0
    assert not any(call[0] == "build" for call in calls)


def test_mutated_runtime_alias_is_not_overwritten_or_built(tmp_path):
    result, calls = invoke(tmp_path, wrong_alias=True)
    assert result.returncode != 0
    assert not any(call[0] == "build" for call in calls)
    assert ["image", "tag", NODE, NODE_ALIAS] not in calls


@pytest.mark.parametrize("alias", [KIT_ALIAS, NODE_ALIAS])
def test_alias_drift_during_build_rejects_result(tmp_path, alias):
    result, calls = invoke(tmp_path, drift=alias)
    assert any(call[0] == "build" for call in calls)
    assert "Traceback" not in result.stderr, (
        "a broken synthetic backend is not a provenance refusal"
    )
    assert result.returncode != 0


def test_matching_content_ancestry_passes_two_immutable_aliases(tmp_path):
    result, calls = invoke(tmp_path)
    assert result.returncode == 0, result.stderr
    build = next(call for call in calls if call[0] == "build")
    assert "--pull=false" in build and "--network=none" in build
    assert f"BASE_IMAGE={KIT_ALIAS}" in build
    assert f"RUNTIME_BASE_IMAGE={NODE_ALIAS}" in build


@pytest.mark.parametrize("settings", [
    {"has_node": False},
    {"runtime_version": "v22.22.3"},
    {"runtime_version": "v24.1.0", "kit_version": "v24.1.0"},
])
def test_ancestor_without_matching_supported_node_fails_before_build(tmp_path, settings):
    result, calls = invoke(tmp_path, **settings)
    assert result.returncode != 0
    assert not any(call[0] == "build" for call in calls)


CA_PARENT = "registry.example.test/platform/max-public-core@sha256:" + "e" * 64
CA_PATH = "usr/local/share/yleum/max-root-ca.pem"
CA_DOCKER = r'''
import copy, hashlib, io, json, os, sys, tarfile
from pathlib import Path
p=Path(os.environ["QA_DOCKER_STATE"]); state=json.loads(p.read_text()); args=sys.argv[1:]
state["calls"].append(args)
try:
 if args[:2]==["image","inspect"]:
  image=state["images"].get(args[2])
  if image is None: sys.exit(1)
  fmt=args[args.index("--format")+1]
  print(image["id"] if fmt=="{{.Id}}" else json.dumps(image))
 elif args[:2]==["image","tag"]:
  state["images"][args[3]]=copy.deepcopy(state["images"][args[2]])
 elif args[0]=="build":
  context=Path(args[-1])
  state["context_files"]=sorted(str(f.relative_to(context))
                                for f in context.rglob("*") if f.is_file())
  state["dockerfile"]=(context/"Dockerfile").read_text()
  cert=(context/"max-root-ca.pem").read_bytes();state["cert_sha256"]=hashlib.sha256(cert).hexdigest()
  layer=io.BytesIO()
  with tarfile.open(fileobj=layer,mode="w") as archive:
   item=tarfile.TarInfo("usr/local/share/yleum/max-root-ca.pem");item.size=len(cert);item.mode=0o444;archive.addfile(item,io.BytesIO(cert))
   if state.get("fault")=="extra_file":
    item=tarfile.TarInfo("app/server.js");item.size=6;archive.addfile(item,io.BytesIO(b"unsafe"))
  state["layer_hex"]=layer.getvalue().hex()
  parent=copy.deepcopy(state["images"][state["parent_ref"]]);parent["id"]="sha256:"+"c"*64
  parent["layers"].append("sha256:"+hashlib.sha256(layer.getvalue()).hexdigest())
  parent["config"]["Env"].append("NODE_EXTRA_CA_CERTS=/usr/local/share/yleum/max-root-ca.pem")
  if state.get("fault")=="cmd_drift":parent["config"]["Cmd"]=["node","different.js"]
  if state.get("fault")=="lineage_drift":parent["layers"][0]="sha256:"+"f"*64
  state["images"][args[args.index("-t")+1]]=parent
  if state.get("fault")=="alias_drift":state["images"][state["alias"]]["id"]="sha256:"+"d"*64
 elif args[:2]==["image","save"]:
  candidate=state["images"][args[-1]];layer=bytes.fromhex(state["layer_hex"])
  cfg=json.dumps({"config":candidate["config"],"rootfs":{"type":"layers","diff_ids":candidate["layers"]}}).encode()
  manifest=json.dumps([{"Config":"config.json","RepoTags":[args[-1]],"Layers":["old1.tar","old2.tar","last.tar"]}]).encode()
  with tarfile.open(args[args.index("--output")+1],"w") as archive:
   for name,data in (("manifest.json",manifest),("config.json",cfg),("last.tar",layer)):
    item=tarfile.TarInfo(name);item.size=len(data);archive.addfile(item,io.BytesIO(data))
 else:sys.exit(2)
finally:p.write_text(json.dumps(state))
'''


def invoke_ca(tmp_path, *, fault=None, parent_change=None, expected_id=None, builder=BUILDER):
    parent_id = "sha256:" + "a" * 64
    alias = "omnia-max-core-ca-base:" + "a" * 64
    parent = {"id": parent_id, "os": "linux", "arch": "amd64",
              "digests": [CA_PARENT], "layers": ["sha256:" + "1" * 64, "sha256:" + "2" * 64],
              "config": {"User": "node", "WorkingDir": "/app", "Cmd": ["node", "server.js"],
                         "Entrypoint": ["docker-entrypoint.sh"], "Env": ["NODE_ENV=production"],
                         "Labels": {"omnia.max-core." + key: "1" for key in
                                    ("protocol", "preview-protocol", "db-role-protocol")}}}
    if parent_change:
        parent_change(parent)
    state = {"images": {CA_PARENT: parent, parent_id: parent}, "calls": [],
             "parent_ref": CA_PARENT, "alias": alias, "fault": fault}
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(state))
    docker = tmp_path / "docker"
    docker.write_text(f"#!{sys.executable}\n" + CA_DOCKER)
    docker.chmod(0o755)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "QA_DOCKER_STATE": str(state_file)}
    args = ["bash", str(builder), "--ca-only", CA_PARENT, OUTPUT, expected_id or parent_id]
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=15)
    return result, json.loads(state_file.read_text())


def test_ca_only_preserves_compiled_parent_and_builds_only_public_cert(tmp_path):
    result, state = invoke_ca(tmp_path)
    assert result.returncode == 0, result.stderr
    assert state["context_files"] == ["Dockerfile", "max-root-ca.pem"]
    instructions = [line.split()[0] for line in state["dockerfile"].splitlines() if line.strip()]
    assert instructions == ["ARG", "FROM", "COPY", "ENV"]
    assert state["cert_sha256"] == (
        "aa800ef345422d6158c6fafe1c06c429dbda21c3df4bb1ccb45a920ec1111399"
    )
    build = next(call for call in state["calls"] if call[0] == "build")
    assert "--pull=false" in build and "--network=none" in build
    assert not any(call[0] in {"pull", "push", "run"} for call in state["calls"])
    assert any(call[:2] == ["image", "save"] for call in state["calls"])


@pytest.mark.parametrize("parent_change", [
    lambda parent: parent.update(digests=[]),
    lambda parent: parent["config"]["Labels"].update({"omnia.max-core.db-role-protocol": "0"}),
    lambda parent: parent["config"].update(Cmd=["pnpm", "start"]),
    lambda parent: parent["config"].update(User="root"),
    lambda parent: parent["config"]["Env"].append("NODE_EXTRA_CA_CERTS=/other.pem"),
])
def test_ca_only_refuses_unverified_or_incompatible_parent_before_build(tmp_path, parent_change):
    result, state = invoke_ca(tmp_path, parent_change=parent_change)
    assert result.returncode != 0
    assert not any(call[0] == "build" for call in state["calls"])


@pytest.mark.parametrize("fault", ["alias_drift", "cmd_drift", "lineage_drift", "extra_file"])
def test_ca_only_refuses_candidate_behavior_or_layer_drift(tmp_path, fault):
    result, state = invoke_ca(tmp_path, fault=fault)
    assert result.returncode != 0
    assert any(call[0] == "build" for call in state["calls"])
    assert "Traceback" not in result.stderr


def test_canonical_builder_passes_official_ca_without_changing_calling_contract(tmp_path):
    result, calls = invoke(tmp_path)
    assert result.returncode == 0, result.stderr
    build = next(call for call in calls if call[0] == "build")
    assert any(value.startswith("MAX_ROOT_CA_BASE64=") for value in build)


def test_ca_only_requires_actual_manifest_to_resolve_exact_host_pin(tmp_path):
    result, state = invoke_ca(tmp_path, expected_id="sha256:" + "f" * 64)
    assert result.returncode != 0
    assert not any(call[0] == "build" for call in state["calls"])


@pytest.mark.parametrize("kind", ["corrupt", "missing", "symlink"])
def test_bad_official_certificate_fails_before_any_docker_call(tmp_path, kind):
    builder = tmp_path / "orchestrator/scripts/build-public-max-core.sh"
    builder.parent.mkdir(parents=True)
    builder.write_bytes(BUILDER.read_bytes())
    certificate = tmp_path / "api/src/yleum_api/certs/russian_trusted_root_ca.pem"
    certificate.parent.mkdir(parents=True)
    if kind == "corrupt":
        certificate.write_text("corrupt public certificate")
    elif kind == "symlink":
        certificate.symlink_to(ROOT.parent / "api/src/yleum_api/certs/russian_trusted_root_ca.pem")
    result, state = invoke_ca(tmp_path, builder=builder)
    assert result.returncode != 0
    assert state["calls"] == []


@pytest.mark.parametrize("corrupt", [False, True])
def test_future_canonical_runtime_hash_checks_decoded_public_certificate(tmp_path, corrupt):
    import base64
    import hashlib

    dockerfile = (ROOT / "scripts/public-max-core/Dockerfile").read_text()
    start = dockerfile.index("RUN mkdir -p /usr/local/share/yleum")
    end = dockerfile.index("\nENV NODE_EXTRA_CA_CERTS=", start)
    command = dockerfile[start + 4:end].replace("/usr/local/share/yleum", str(tmp_path / "trust"))
    certificate = (ROOT.parent / "api/src/yleum_api/certs/russian_trusted_root_ca.pem").read_bytes()
    encoded = base64.b64encode(b"corrupt" if corrupt else certificate).decode()
    result = subprocess.run(["bash", "-c", command],
                            env={**os.environ, "MAX_ROOT_CA_BASE64": encoded},
                            capture_output=True, text=True, timeout=5)
    assert (result.returncode != 0) is corrupt
    if not corrupt:
        saved = tmp_path / "trust/max-root-ca.pem"
        assert hashlib.sha256(saved.read_bytes()).digest() == hashlib.sha256(certificate).digest()
        assert saved.stat().st_mode & 0o777 == 0o444
    assert "ENV NODE_EXTRA_CA_CERTS=/usr/local/share/yleum/max-root-ca.pem" in dockerfile
