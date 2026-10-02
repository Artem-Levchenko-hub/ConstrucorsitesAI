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
