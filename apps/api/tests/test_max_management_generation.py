"""New management is explicit; legacy config/data never silently gains privileges."""

import json
from pathlib import Path
from subprocess import run
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yleum_api.schemas.max_studio import MaxProjectConfigPayload
from yleum_api.services.max_content_delivery import public_business_config
from yleum_api.services.max_management_contract import build_management_contract
from yleum_api.services.max_management_generation import (
    management_implemented_capabilities,
    management_source_gap,
)
from yleum_api.services.max_project_kit import (
    MAX_SECURITY_LOCKED_FILES,
    render_max_managed_files,
)


def payload(**extra):
    return MaxProjectConfigPayload.model_validate(
        {
            "app_name": "Мастерская",
            "app_type": "booking",
            "summary": "Запрос на запись к мастеру",
            **extra,
        }
    )


def test_legacy_config_has_no_implicit_business_management():
    config = payload()
    assert config.management_modules is None
    assert public_business_config(config).get("management_modules") is None


@pytest.mark.parametrize(
    "modules",
    [
        ["orders"],
        ["bookings"],
        ["catalog", "orders", "orders"],
        ["private_diaries"],
        [True],
        "orders",
    ],
)
def test_invalid_management_selection_is_rejected(modules):
    with pytest.raises(ValidationError):
        payload(management_modules=modules)


def test_management_selection_is_private_and_catalog_patch_preserves_it():
    config = payload(management_modules=["bookings", "catalog", "leads"])
    assert [str(item.value) for item in config.management_modules] == [
        "catalog",
        "leads",
        "bookings",
    ]
    config.content = []
    saved = MaxProjectConfigPayload.model_validate(config.model_dump(mode="json"))
    assert saved.management_modules == config.management_modules
    assert "management_modules" not in public_business_config(config)
    files = render_max_managed_files(config, uuid4())
    public_source = files["src/lib/omnia/max-config.ts"]
    assert '"management_modules"' not in public_source
    manifest = json.loads(files[".omnia/management.json"])
    assert manifest["modules"] == ["catalog", "leads", "bookings"]
    assert [r["id"] for r in manifest["resources"]] == ["leads", "bookings"]


def test_new_runtime_manifest_and_sdk_are_platform_owned():
    files = render_max_managed_files(payload(management_modules=["catalog", "orders"]), uuid4())
    expected = {
        ".omnia/management.json",
        "src/lib/omnia/management-config.ts",
        "src/lib/omnia/management-auth.ts",
        "src/lib/omnia/management-store.ts",
        "src/lib/omnia/management-resources.ts",
        "src/lib/omnia/business-client.ts",
        "src/lib/omnia/business-server.ts",
        "src/app/api/omnia/owner/route.ts",
        "src/app/api/omnia/business/[resource]/route.ts",
        "drizzle/0003_owner_management.sql",
        "src/lib/omnia/app-profile-client.ts",
        "src/lib/omnia/app-profile-actions.ts",
    }
    assert expected <= files.keys()
    assert expected <= MAX_SECURITY_LOCKED_FILES
    assert "useYleumAppProfile" in files["src/lib/omnia/app-profile-client.ts"]


def test_legacy_managed_kit_has_no_order_capability():
    files = render_max_managed_files(payload(), uuid4())
    assert ".omnia/management.json" not in files
    assert "drizzle/0003_owner_management.sql" not in files


CONNECTION_MODULES = (
    (), ("leads",), ("catalog",), ("catalog", "leads"), ("catalog", "orders"),
    ("catalog", "bookings"), ("catalog", "orders", "leads"),
    ("catalog", "orders", "bookings"), ("catalog", "leads", "bookings"),
    ("catalog", "orders", "leads", "bookings"),
)
CREATE_METHOD = {
    "orders": "createManagedOrder", "leads": "createManagedLead",
    "bookings": "createManagedBooking",
}


def connection_example(modules):
    """Synthetic typed connection shapes, not a complete app or business inputs."""
    declarations = ["const {profile} = useYleumAppProfile();"]
    rendered = ["<h1>{profile?.app_name}</h1>"]
    inputs = []
    if "catalog" in modules:
        inputs.append("onChoose: (item: YleumMaxContentItem, option?: string) => void")
        declarations += [
            "const catalog = useYleumCatalog();",
            "const selection = useYleumCatalogSelection(catalog.items);",
            "const ready = catalog.status === 'ready' && catalog.configVersion !== null;",
        ]
        rendered += [
            '<p role="status">{catalog.status === "loading" ? "Loading catalog" : ""}</p>',
            '<p role="alert">{catalog.error}</p>',
            """{catalog.items.map(item => item.active &&
              (selection.category === null || item.category === selection.category) ?
              <article key={item.id}><h2>{item.title}</h2><span>{item.price}</span>
                <button disabled={!ready || item.availability !== "in_stock" ||
                  (item.options.length > 0 && !selection.getOption(item))}
                  onClick={() => inputs.onChoose(item, selection.getOption(item))}>Choose</button>
              </article> : null)}""",
        ]
    for resource, method in CREATE_METHOD.items():
        if resource not in modules:
            continue
        inputs.append(f"{resource}: Parameters<typeof {method}>[0]")
        declarations += [
            f"const [{resource}, set_{resource}] = useState<ManagedRecord[]>([]);",
            f"""useEffect(() => {{ async function load() {{
              const response = await getManagedRecords('{resource}', {{limit: 50}});
              set_{resource}(response.records);
            }} void load(); }}, []);""",
            f"""async function submit_{resource}(event: FormEvent<HTMLFormElement>) {{
              event.preventDefault(); await {method}({{...inputs.{resource},
                operationKey: inputs.{resource}.operationKey}});
            }}""",
        ]
        rendered += [
            f'<form onSubmit={{submit_{resource}}}><button>Send {resource}</button></form>',
            f"{{{resource}.map(record => <p key={{record.id}}>"
            "{record.customer_name} {record.status}</p>)}",
        ]
    source = """
import {useEffect, useState, type FormEvent} from 'react';
import {useYleumAppProfile} from '@/lib/omnia/app-profile-client';
import {useYleumCatalog, useYleumCatalogSelection} from '@/lib/omnia/catalog-client';
import {createManagedOrder, createManagedLead, createManagedBooking,
  getManagedRecords} from '@/lib/omnia/business-client';
import type {ManagedRecord} from '@/lib/omnia/management-resources';
import type {YleumMaxContentItem} from '@/lib/omnia/max-config';
"""
    return (
        source + "\nexport default function Connections(inputs: {" + ";".join(inputs) + "}) {\n"
        + "\n".join(declarations) + "\nreturn <main>" + "\n".join(rendered) + "</main>;\n}\n"
    )


def connection_files(source, modules):
    return {
        ".omnia/management.json": build_management_contract(modules).model_dump_json(),
        "src/app/page.tsx": source,
    }


@pytest.mark.parametrize("modules", CONNECTION_MODULES)
def test_supported_direct_jsx_connections_cover_every_valid_module_combination(modules):
    files = connection_files(connection_example(modules), modules)
    expected = set()
    if "catalog" in modules:
        expected.add("catalog")
    if set(modules) & CREATE_METHOD.keys():
        expected.add("history")
    if "bookings" in modules:
        expected.add("booking")
    assert management_source_gap(files) is None
    assert management_implemented_capabilities(files) == frozenset(expected)


@pytest.mark.parametrize("before,after", [
    ("{profile?.app_name}", "Static app"),
    ("<h2>{item.title}</h2><span>{item.price}</span>", "<h2>Static item</h2>"),
    ("useYleumCatalogSelection(catalog.items)", "useYleumCatalogSelection([])"),
    ("await createManagedOrder(", "await Promise.resolve("),
    ("set_orders(response.records)", "set_orders([])"),
])
def test_disconnected_connections_keep_original_reason_and_explain_supported_shape(before, after):
    modules = ("catalog", "orders")
    source = connection_example(modules).replace(before, after)
    gap = management_source_gap(connection_files(source, modules))
    assert gap is not None and gap.startswith("Connect ")
    assert "cannot prove" in gap
    assert "direct JSX" in gap and "useMemo" in gap
    assert not management_implemented_capabilities(connection_files(source, modules))


@pytest.mark.parametrize("resource,method", CREATE_METHOD.items())
@pytest.mark.parametrize("disconnect", ["create", "history"])
def test_each_business_module_rejects_disconnected_form_or_history(resource, method, disconnect):
    modules = ("catalog", resource)
    source = connection_example(modules)
    if disconnect == "create":
        source = source.replace(f"await {method}(", "await Promise.resolve(")
    else:
        source = source.replace(f"set_{resource}(response.records)", f"set_{resource}([])")
    gap = management_source_gap(connection_files(source, modules))
    assert gap is not None and gap.startswith(f"Connect {method}")
    assert "cannot prove" in gap


def unsupported_memo_example():
    return (
        connection_example(("catalog",))
        .replace("useEffect, useState", "useEffect, useMemo, useState")
        .replace("const ready =", "const shown = useMemo(() => catalog.items, [catalog.items]);\n"
                 "const ready =")
        .replace("catalog.items.map", "shown.map")
    )


def test_valid_but_unsupported_memo_keeps_the_fail_closed_subset():
    gap = management_source_gap(connection_files(unsupported_memo_example(), ("catalog",)))
    assert gap is not None and gap.startswith("Connect useYleumCatalog")
    assert "cannot prove" in gap and "useMemo" in gap
    assert management_source_gap({"src/app/page.tsx": "export default () => <div/>"}) is None
    invalid = {"src/app/page.tsx": "", ".omnia/management.json": "invalid"}
    assert management_source_gap(invalid).startswith("Invalid .omnia/management.json:")


def test_connection_examples_typecheck_against_real_sdk(tmp_path):
    apps = Path(__file__).resolve().parents[2]
    modules = apps / "web/node_modules"
    template = apps / "orchestrator/templates/max-miniapp-nextjs"
    compiler = template / "node_modules/typescript/lib/tsc.js"
    if not compiler.is_file():
        compiler = modules / "typescript/lib/tsc.js"
    if not compiler.is_file() or not (template / "node_modules/@types/pg/index.d.ts").is_file():
        pytest.skip("Real TypeScript/managed-template dependencies are not installed")
    for source in (template / "src").rglob("*"):
        if source.is_file() and source.suffix in {".ts", ".tsx"}:
            target = tmp_path / source.relative_to(template)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    for relative, content in render_max_managed_files(payload(), uuid4()).items():
        if relative.endswith((".ts", ".tsx")):
            target = tmp_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    for index, selected in enumerate(CONNECTION_MODULES):
        (tmp_path / f"connection-{index}.tsx").write_text(
            connection_example(selected), encoding="utf-8",
        )
    (tmp_path / "connection-unsupported.tsx").write_text(
        unsupported_memo_example(), encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(json.dumps({
        "compilerOptions": {
            "strict": True, "target": "ES2022", "module": "ESNext", "jsx": "react-jsx",
            "moduleResolution": "Bundler", "noEmit": True, "skipLibCheck": True,
            "baseUrl": str(tmp_path), "paths": {
                "@/*": ["src/*"],
                "react": [str(template / "node_modules/@types/react/index.d.ts")],
                "react/*": [str(template / "node_modules/@types/react/*")],
                "pg": [str(template / "node_modules/@types/pg/index.d.ts")],
                "*": [str(template / "node_modules/*"), str(modules / "*")],
            },
            "typeRoots": [str(template / "node_modules/@types"), str(modules / "@types")],
            "types": ["node", "react"],
        },
        "include": ["connection-*.tsx"],
    }), encoding="utf-8")
    result = run(["node", str(compiler), "--project", str(tmp_path)],
                 capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
