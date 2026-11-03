"""The mandatory MAX SDK contract reaches provider requests and compiles as supplied."""

import json
import re
from pathlib import Path
from subprocess import run
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yleum_api.schemas.max_studio import MaxProjectConfigPayload
from yleum_api.services import agent_builder, agent_native
from yleum_api.services.max_data_evolution import build_max_agent_guide
from yleum_api.services.max_project_kit import (
    MAX_MANAGED_SDK_GUIDANCE,
    MAX_MODEL_DIRECTIVE,
    _template_file,
    render_max_managed_files,
)


class ProviderRequestCaptured(BaseException):
    """Stop at the provider boundary, before any network or tool execution."""


@pytest.mark.parametrize("mode", ["build", "edit", "native"])
@pytest.mark.parametrize("provider", ["legacy", "cell-legacy", "portable", "missing-manifest"])
async def test_provider_request_contains_one_managed_sdk_contract(monkeypatch, mode, provider):
    executor = (
        None if provider == "legacy" else SimpleNamespace(
            capabilities={"portable_machine": provider != "cell-legacy"},
            snapshot_files=AsyncMock(return_value=(
                {} if provider == "missing-manifest" else {".omnia/cell.json": "{}"}
            )),
        )
    )
    guide = await build_max_agent_guide(MAX_MODEL_DIRECTIVE, executor)
    captured = []

    async def legacy_provider(messages, model, **kwargs):
        captured.extend(message["content"] for message in messages if message["role"] == "system")
        raise ProviderRequestCaptured

    async def native_provider(client, url, convo, system, **kwargs):
        captured.append(system)
        raise ProviderRequestCaptured

    async def unexpected_tool(action):
        pytest.fail(f"No tool should run before the first provider request: {action.name}")

    with pytest.raises(ProviderRequestCaptured):
        if mode == "native":
            monkeypatch.setattr(agent_native, "_call_messages", native_provider)
            await agent_native.run_native_build(
                system=agent_native.native_system_prompt(guide), task="Build the catalog",
                execute=unexpected_tool, portable_cell=provider == "portable", max_steps=1,
            )
        else:
            builder = (
                agent_builder.build_edit_system_prompt if mode == "edit"
                else agent_builder.build_system_prompt
            )
            await agent_builder.run_agent_build(
                system_prompt=builder(guide), user_prompt="Build the catalog",
                model="test-model", execute=unexpected_tool, complete=legacy_provider,
                edit_mode=mode == "edit", max_steps=1,
            )
    assert len(captured) == 1
    prompt = captured[0]
    assert prompt.count("MAX MANAGED SDK CONTRACT") == 1
    for required in (
        "profile?.app_name", "item.title", "useYleumCatalogSelection(catalog.items)",
        "createManagedOrder", "createManagedLead", "createManagedBooking",
        "loadPendingManagedIntent(resource)", "getManagedRecords(resource,{limit,cursor})",
        "quote_changed", "intent_storage_unavailable", "current customer's records",
    ):
        assert required in prompt, required


def test_supplied_sdk_example_typechecks_against_current_template_hooks(tmp_path):
    modules = (
        Path(__file__).resolve().parents[2]
        / "orchestrator/templates/max-miniapp-nextjs/node_modules"
    )
    compiler = modules / "typescript/lib/tsc.js"
    if not compiler.is_file():
        pytest.skip("TypeScript compiler is not installed")

    snippet = re.search(r"```tsx\n(.*?)\n```", MAX_MANAGED_SDK_GUIDANCE, re.DOTALL)
    assert snippet is not None, "The SDK contract must supply a typed connection example"
    probe = tmp_path / "probe.tsx"
    probe.write_text(snippet[1], encoding="utf-8")
    config_path = "src/lib/omnia/max-config.ts"
    files = {
        f"src/lib/omnia/{name}.ts": _template_file(f"src/lib/omnia/{name}.ts")
        for name in (
            "app-profile-client", "app-profile-actions", "catalog-client", "catalog-actions",
            "catalog-server",
        )
    }
    files[config_path] = render_max_managed_files(
        MaxProjectConfigPayload.default_for("SDK contract fixture"),
    )[config_path]
    for path, content in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text(json.dumps({"compilerOptions": {
        "strict": True, "target": "ES2022", "module": "ESNext", "jsx": "react-jsx",
        "moduleResolution": "Bundler", "noEmit": True, "skipLibCheck": True,
        "baseUrl": str(tmp_path), "paths": {
            "@/*": ["src/*"], "react": [str(modules / "@types/react")],
            "react/jsx-runtime": [str(modules / "@types/react/jsx-runtime")],
            "zod": [str(modules / "zod")],
        },
        "typeRoots": [str(modules / "@types")], "types": ["node", "react"],
    }}), encoding="utf-8")

    def typecheck():
        return run(
            ["node", str(compiler), "--project", str(tmp_path)],
            capture_output=True, text=True, check=False, timeout=30,
        )

    valid = typecheck()
    assert valid.returncode == 0, valid.stdout + valid.stderr
    # The exact observed regression must fail against the real supplied SDK types.
    probe.write_text(
        snippet[1].replace("profile?.app_name", "profile?.name").replace("item.title", "item.name"),
        encoding="utf-8",
    )
    invalid = typecheck()
    assert invalid.returncode != 0
    assert "TS2339" in invalid.stdout
    assert "'name' does not exist on type 'YleumAppProfile'" in invalid.stdout
    assert "'name' does not exist on type 'YleumMaxContentItem'" in invalid.stdout
