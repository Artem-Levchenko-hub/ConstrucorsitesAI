import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from yleum_orchestrator.core.project_machine import MachineManifest
from yleum_orchestrator.services.next_compilation_capture import (
    capture_next_compilation,
    full_build_tasks,
)


def setup(receipt=None):
    helper = NS(
        start=lambda: None,
        wait=lambda **kw: {"StatusCode": 0},
        logs=lambda **kw: json.dumps(receipt).encode(),
        removed=[],
        remove=lambda **kw: helper.removed.append(kw),
    )
    containers = NS(calls=[], create=lambda *a, **kw: containers.calls.append((a, kw)) or helper)
    backend = NS(
        base_image="image@sha256:" + "a" * 64,
        workspace_volume="owned-volume",
        workspace_id=uuid4(),
        project_id=uuid4(),
        stem="owned",
        labels=lambda kind: {"role": kind},
        client=NS(containers=containers),
    )
    request = NS(
        operation_id=uuid4(), generation_run_id=uuid4(), fencing_epoch=7, expected_revision="b" * 64
    )
    manifest = NS(
        tasks=[NS(role="full_build", argv=["pnpm", role], cwd=".") for role in ["build", "test"]],
        digest=lambda: "c" * 64,
    )
    return backend, request, manifest, helper, containers


def managed_manifest(
    commands, *, cwd=".", protocol=True, test_script="node --test tests/*.mjs", files=(),
):
    from tests.test_project_machine_manifest import payload

    value = payload()
    if protocol:
        value["app_config_protocol"] = 1
    value["tasks"] = [
        {"name": f"run-{index}", "role": "full_build", "argv": argv, "cwd": cwd}
        for index, argv in enumerate(commands)
    ]
    manifest = MachineManifest.from_files({
        ".omnia/cell.json": json.dumps(value),
        "package.json": json.dumps({"dependencies": {"next": "15.5.24"}, "scripts": {
            "build": "next build", "test": test_script,
        }}),
        **dict.fromkeys(files, "// product test"),
    })
    assert manifest is not None
    return manifest


@pytest.mark.parametrize("mode", ["omitted", "direct", "opt_in", "duplicate", "empty"])
def test_physical_plan_supplies_missing_next_suite_and_binds_receipt(mode):
    build, package = ["pnpm", "build"], ["pnpm", "test"]
    opt_in = ["env", "SHOPPING_TEST_DATABASE=1", *package]
    product = ["node", "--test", "tests/shopping.integration.mjs"]
    commands, expected = {
        "omitted": ([build], [build, package]),
        "empty": ([], [build, package]),
        "direct": ([build, product], [build, package, product]),
        "opt_in": ([build, package, opt_in, product], [build, package, opt_in, product]),
        "duplicate": ([build, build, package, product, ["pnpm", "exec", *product]],
                      [build, package, product]),
    }[mode]
    manifest = managed_manifest(commands)
    backend, request, _, _, _ = setup({"collector_version": "next-fixed-data-v1"})
    assert [task.argv for task in full_build_tasks(manifest)] == expected
    assert capture_next_compilation(backend, request, manifest)["manifest_digest"] == (
        manifest.digest()
    )
    # Derived package evidence is neither writable in cell.json nor part of its canonical hash.
    assert MachineManifest.model_validate_json(manifest.canonical_json()).digest() == (
        manifest.digest()
    )


@pytest.mark.parametrize("kwargs,commands", [
    ({"cwd": "app"}, [["pnpm", "build"]]),
    ({"protocol": False}, [["pnpm", "build"]]),
    ({}, [["sh", "-lc", "pnpm build && pnpm custom-suite"]]),
])
def test_custom_release_preserves_exact_commands(kwargs, commands):
    manifest = managed_manifest(commands, **kwargs)
    assert [task.argv for task in full_build_tasks(manifest)] == commands


@pytest.mark.parametrize("package,expect_extra", [
    (["pnpm", "test"], False),
    (["env", "SHOPPING_TEST_DATABASE=1", "pnpm", "test"], True),
])
def test_literal_product_command_runs_once_per_explicit_database_mode(package, expect_extra):
    product = ["node", "--test", "tests/shopping.integration.mjs"]
    manifest = managed_manifest(
        [["pnpm", "build"], package, ["pnpm", "exec", *product]], test_script=" ".join(product),
    )
    plan = [task.argv for task in full_build_tasks(manifest)]
    expected = [["pnpm", "build"], package]
    if expect_extra:
        expected.append(["pnpm", "exec", *product])
    assert plan == expected


@pytest.mark.parametrize("reverse", [True, False])
def test_explicit_package_database_modes_keep_original_order(reverse):
    modes = [
        ["pnpm", "test"], ["env", "A_TEST_DATABASE=1", "pnpm", "test"],
        ["env", "B_TEST_DATABASE=1", "pnpm", "test"],
    ]
    if reverse:
        modes.reverse()
    manifest = managed_manifest([*modes, modes[0]])
    assert [t.argv for t in full_build_tasks(manifest)] == [["pnpm", "build"], *modes]
    backend, request, _, _, _ = setup({"collector_version": "next-fixed-data-v1"})
    assert capture_next_compilation(backend, request, manifest) is not None


@pytest.mark.parametrize("extra_product", [False, True])
def test_legacy_managed_build_uses_the_package_and_preserves_its_explicit_test(extra_product):
    product = ["node", "tests/legacy.mjs"]
    commands = [["pnpm", "build"], product] if extra_product else [["pnpm", "build"]]
    manifest = managed_manifest(commands)
    manifest.tasks[0].role = "build"
    if extra_product:
        manifest.tasks[1].role = "test"
        manifest.tasks[1].timeout_seconds = 270
        manifest.tasks.reverse()
    plan = full_build_tasks(manifest)
    assert [task.argv for task in plan] == [["pnpm", "build"], ["pnpm", "test"]] + (
        [product] if extra_product else []
    )
    assert all(task.role == "full_build" for task in plan)
    if extra_product:
        assert plan[-1].timeout_seconds == 270 and plan[-1].name == "run-1"


@pytest.mark.parametrize("package,retained", [
    (["pnpm", "test"], False), (["env", "SHOPPING_TEST_DATABASE=1", "pnpm", "test"], True),
])
def test_standard_glob_only_subsumes_an_existing_immediate_file_in_the_same_mode(package, retained):
    command = ["node", "--test", "tests/product.test.mjs"]
    missing = ["node", "--test", "tests/missing.test.mjs"]
    nested = ["node", "--test", "tests/server/other.test.mjs"]
    plain_node = ["node", "tests/product.test.mjs"]
    sources = ["tests/product.test.mjs", "tests/server/other.test.mjs"]
    manifest = managed_manifest(
        [package, command, missing, nested, plain_node], test_script="node --test tests/*.test.mjs",
        files=sources,
    )
    expected = [["pnpm", "build"], package] + ([command] if retained else [])
    assert [t.argv for t in full_build_tasks(manifest)] == [*expected, missing, nested, plain_node]


def test_managed_compound_suite_receipt_and_additional_distinct_product_test():
    shell = ["sh", "-lc", "pnpm test && node --test tests/shopping.integration.mjs"]
    repeated = ["node", "--test", "tests/shopping.integration.mjs"]
    extra = ["node", "--test", "tests/persistence.mjs"]
    manifest = managed_manifest([shell, repeated, extra])
    assert [t.argv for t in full_build_tasks(manifest)] == [["pnpm", "build"], shell, extra]
    backend, request, _, _, _ = setup({"collector_version": "next-fixed-data-v1"})
    assert capture_next_compilation(backend, request, manifest) is not None


@pytest.mark.parametrize(
    "test_commands",
    [
        pytest.param([["pnpm", "test"]], id="package-test"),
        pytest.param(
            [["env", "SHOPPING_TEST_DATABASE=1", "pnpm", "test"]],
            id="project-database-test",
        ),
        pytest.param(
            [[
                "sh",
                "-lc",
                "pnpm test && node --test tests/server/cache.test.mjs ./tests/health.test.js"
                " && node --test tests/legacy.test.cjs",
            ]],
            id="explicit-node-suites",
        ),
        pytest.param(
            [["pnpm", "test"], ["node", "tests/split-contract.mjs"]],
            id="direct-node-script",
        ),
        pytest.param(
            [
                ["pnpm", "test"],
                ["pnpm", "exec", "node", "--test", "tests/shopping.integration.mjs"],
            ],
            id="package-and-project-integration",
        ),
        pytest.param(
            [
                ["pnpm", "test"],
                ["node", "--test", "./tests/server/one.cjs", "tests/two.js"],
                ["node", "tests/another.mjs"],
            ],
            id="direct-node-suites",
        ),
    ],
)
def test_capture_uses_only_exact_ro_owned_volume_and_platform_interpreter(test_commands):
    backend, request, manifest, helper, containers = setup(
        {"collector_version": "next-fixed-data-v1", "assets": []}
    )
    manifest.tasks[1:] = [NS(role="full_build", argv=argv, cwd=".") for argv in test_commands]
    receipt = capture_next_compilation(backend, request, manifest)
    assert receipt is not None
    args, kw = containers.calls[0]
    assert args[0] == backend.base_image and args[1][:4] == ["python3", "-I", "-S", "-c"]
    for argv in test_commands:
        if argv[0] in {"sh", "node"} or argv[:2] == ["pnpm", "exec"]:
            assert argv[-1] not in args[1][-1]
        if argv[0] == "env":
            assert argv[1] not in args[1][-1]
    assert kw["volumes"] == {"owned-volume": {"bind": "/proof", "mode": "ro"}}
    assert kw["network_mode"] == "none" and kw["read_only"] is True and kw["cap_drop"] == ["ALL"]
    assert "environment" not in kw and "privileged" in kw and not kw["privileged"]
    assert (
        receipt["operation_id"] == str(request.operation_id)
        and receipt["source_revision"] == request.expected_revision
    )
    assert helper.removed == [{"force": True}]


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("node --test tests/product.mjs", id="missing-package-test"),
        pytest.param("pnpm test && node --test", id="implicit-suite-discovery"),
        pytest.param("pnpm test || node --test tests/product.mjs", id="ignored-package-failure"),
        pytest.param("pnpm test && node --test tests/product.mjs; true", id="shell-sequence"),
        pytest.param("pnpm test && node --test tests/product.mjs\ntrue", id="newline"),
        pytest.param("pnpm test && node --test tests/*.mjs", id="glob"),
        pytest.param("pnpm test && node --test /tests/product.mjs", id="absolute-path"),
        pytest.param("pnpm test && node --test tests/../product.mjs", id="traversal"),
        pytest.param("pnpm test && node --test tests/product.mjs --test-only", id="flag"),
        pytest.param("pnpm test && node --test tests/product.mjs && true", id="unknown-command"),
    ],
)
def test_unsupported_final_test_never_starts_compilation_capture(command):
    backend, request, manifest, _helper, containers = setup(
        {"collector_version": "next-fixed-data-v1"}
    )
    manifest.tasks[1].argv = ["sh", "-lc", command]

    assert capture_next_compilation(backend, request, manifest) is None
    assert not containers.calls


@pytest.mark.parametrize(
    "argv",
    [
        ["env", "SHOPPING_TEST_DATABASE=0", "pnpm", "test"],
        ["env", "SHOPPING_TEST_DATABASE=1", "pnpm", "build"],
        ["env", "PATH=1", "pnpm", "test"],
        ["env", "SHOPPING_TEST_DATABASE=1", "-i", "pnpm", "test"],
    ],
)
def test_database_test_opt_in_requires_the_package_test(argv):
    backend, request, manifest, _helper, containers = setup()
    manifest.tasks[1].argv = argv
    assert capture_next_compilation(backend, request, manifest) is None
    assert not containers.calls


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["node", "tests/one.mjs", "tests/two.mjs"], id="plain-extra-argument"),
        pytest.param(["node", "--test"], id="implicit-suite-discovery"),
        pytest.param(["node", "--test", "tests/a.mjs", "--test-only"], id="flag"),
        pytest.param(["node", "--test", "tests/*.mjs"], id="glob"),
        pytest.param(["node", "tests/sub/../a.mjs"], id="traversal"),
        pytest.param(["node", "tests//a.mjs"], id="unnormalized-path"),
        pytest.param(["node", "/tests/a.mjs"], id="absolute-path"),
        pytest.param(["node", "src/a.mjs"], id="outside-tests"),
        pytest.param(["sh", "-lc", "node --test tests/a.mjs"], id="shell-extension"),
        pytest.param(["pnpm", "test"], id="duplicate-package-test"),
        pytest.param(["pnpm", "exec", "node", "--test", "src/a.mjs"], id="exec-outside-tests"),
        pytest.param(["pnpm", "exec", "node", "--test"], id="exec-implicit-suite"),
    ],
)
def test_unsupported_direct_test_never_starts_compilation_capture(argv):
    backend, request, manifest, _helper, containers = setup()
    manifest.tasks.append(NS(role="full_build", argv=argv, cwd="."))

    assert capture_next_compilation(backend, request, manifest) is None
    assert not containers.calls


@pytest.mark.parametrize("invalid", ["order", "shell-mix", "cwd", "standalone-exec"])
def test_direct_test_requires_root_package_test_first(invalid):
    backend, request, manifest, _helper, containers = setup()
    direct = NS(role="full_build", argv=["node", "tests/a.mjs"], cwd=".")
    manifest.tasks.append(direct)
    if invalid == "order":
        manifest.tasks[1:] = reversed(manifest.tasks[1:])
    elif invalid == "shell-mix":
        manifest.tasks[1].argv = ["sh", "-lc", "pnpm test && node --test tests/primary.mjs"]
    elif invalid == "standalone-exec":
        manifest.tasks[1:] = [
            NS(role="full_build", argv=["pnpm", "exec", "node", "--test", "tests/a.mjs"], cwd=".")
        ]
    else:
        direct.cwd = "tests"

    assert capture_next_compilation(backend, request, manifest) is None
    assert not containers.calls


@pytest.mark.parametrize(
    "mutation", ["unknown-command", "unpin", "invalid-data", "oversize", "failed"]
)
def test_unknown_or_failed_capture_never_supplies_receipt(mutation):
    backend, request, manifest, helper, containers = setup(
        {"collector_version": "next-fixed-data-v1"}
    )
    if mutation == "unknown-command":
        manifest.tasks[0].argv = ["node", "model.js"]
    if mutation == "unpin":
        backend.base_image = "image:latest"
    if mutation == "invalid-data":
        helper.logs = lambda **kw: b"generated raw output"
    if mutation == "oversize":
        helper.logs = lambda **kw: b"x" * 65537
    if mutation == "failed":
        helper.wait = lambda **kw: {"StatusCode": 1}
    assert capture_next_compilation(backend, request, manifest) is None
    assert (
        not containers.calls
        if mutation in {"unknown-command", "unpin"}
        else helper.removed == [{"force": True}]
    )


@pytest.mark.parametrize("version", ["next-restored-runtime-v1", "next-fixed-data-v1"])
@pytest.mark.parametrize(
    "test_commands",
    [
        [["pnpm", "test"]],
        [["env", "SHOPPING_TEST_DATABASE=1", "pnpm", "test"]],
        [["pnpm", "test"], ["pnpm", "exec", "node", "--test", "tests/shopping.integration.mjs"]],
    ],
)
def test_restore_capture_has_distinct_receipt_and_allows_core_only_build(version, test_commands):
    backend, request, manifest, helper, containers = setup({"collector_version": version})
    request.task_role = "restore_runtime"
    manifest.tasks[1:] = [NS(role="full_build", argv=argv, cwd=".") for argv in test_commands]
    receipt = capture_next_compilation(backend, request, manifest)
    script = containers.calls[0][0][1][-1]
    assert "collect_next_compilation(require_product_page=False)" in script
    assert (receipt is not None) is (version == "next-restored-runtime-v1")
    assert helper.removed == [{"force": True}]
