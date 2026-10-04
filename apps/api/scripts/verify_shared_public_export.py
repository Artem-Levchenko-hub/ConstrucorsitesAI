"""Verify mounted container-template exports from the installed API, without network."""

import argparse
import hashlib
import json
from pathlib import Path

from yleum_api.services import project_export

# Frozen API export omissions at 0dacd38f, independent of the current reader.
# Orchestrator seeds include these internal skills; downloaded projects do not.
_EXPORT_OMISSIONS = {
    "max-miniapp-nextjs": {".omnia/skills/INDEX.md", ".omnia/skills/max-ui-design.md"},
    "nextjs-entities": set(),
    "nextjs-postgres-drizzle": {
        ".omnia/skills/INDEX.md",
        ".omnia/skills/a11y.md",
        ".omnia/skills/perf.md",
        ".omnia/skills/security.md",
    },
    "nextjs-realtime": {".omnia/skills/INDEX.md", ".omnia/skills/realtime.md"},
}


# Only reviewed dependency/build files may replace the historical MAX golden.
# Separate reviewed action overrides pin the changed managed SDK/route bytes.
_DEPENDENCY_OVERRIDE_PATHS = {
    "Dockerfile.dev",
    "package.json",
    "pnpm-lock.yaml",
    "tests/database-compatibility.test.mjs",
    "tests/dependency-install.test.mjs",
}


_ACTION_WRITE_OVERRIDE_PATHS = {
    "src/app/api/omnia/actions/route.ts",
    "src/app/api/omnia/actions/[id]/route.ts",
    "src/lib/omnia/integration-client.ts",
    "src/lib/omnia/client.ts",
    "tests/starter.test.mjs",
}


_PREVIEW_RENEWAL_OVERRIDE_PATHS = {
    "src/app/api/max/session/route.ts",
    "src/app/api/omnia/preview-session/route.ts",
    "src/components/MaxAppProvider.tsx",
    "src/lib/max/owner-preview-renewal.ts",
}


_ANALYTICS_OVERRIDE_PATHS = {
    "src/app/api/max/session/route.ts",
    "src/app/api/omnia/actions/route.ts",
    "src/app/api/omnia/events/route.ts",
    "src/lib/omnia/analytics.ts",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("golden", type=Path)
    parser.add_argument("readme_overrides", type=Path)
    parser.add_argument("dependency_overrides", type=Path)
    parser.add_argument("action_write_overrides", type=Path)
    parser.add_argument("support_overrides", type=Path)
    parser.add_argument("preview_renewal_overrides", type=Path)
    parser.add_argument("analytics_overrides", type=Path)
    args = parser.parse_args()
    golden = json.loads(args.golden.read_text(encoding="utf-8"))["templates"]
    overrides = json.loads(args.readme_overrides.read_text(encoding="utf-8"))
    dependency_overrides = json.loads(args.dependency_overrides.read_text(encoding="utf-8"))
    assert set(dependency_overrides) == _DEPENDENCY_OVERRIDE_PATHS, (
        "unexpected MAX dependency override paths"
    )
    action_write_overrides = json.loads(args.action_write_overrides.read_text(encoding="utf-8"))
    assert set(action_write_overrides) == _ACTION_WRITE_OVERRIDE_PATHS, (
        "unexpected MAX action write override paths"
    )
    support_overrides = json.loads(args.support_overrides.read_text(encoding="utf-8"))
    assert set(support_overrides) == {"src/app/support/page.tsx"}, (
        "unexpected support override paths"
    )
    preview_renewal_overrides = json.loads(
        args.preview_renewal_overrides.read_text(encoding="utf-8")
    )
    assert set(preview_renewal_overrides) == _PREVIEW_RENEWAL_OVERRIDE_PATHS, (
        "unexpected MAX preview renewal override paths"
    )
    analytics_overrides = json.loads(args.analytics_overrides.read_text(encoding="utf-8"))
    assert set(analytics_overrides) == _ANALYTICS_OVERRIDE_PATHS, (
        "unexpected MAX analytics override paths"
    )
    for name, complete_tree in golden.items():
        if name == "max-miniapp-nextjs":
            complete_tree = (
                complete_tree
                | dependency_overrides
                | preview_renewal_overrides
                | analytics_overrides
            )
        omissions = _EXPORT_OMISSIONS[name]
        assert omissions <= complete_tree.keys(), f"unknown frozen export omissions: {name}"
        expected = {path: entry for path, entry in complete_tree.items() if path not in omissions}
        exported = project_export.build_runnable_export(name, {})
        exported.pop("README.omnia.md", None)
        assert exported.keys() == expected.keys(), f"incomplete standalone export: {name}"
        for relative, entry in expected.items():
            entry = overrides.get(f"{name}/{relative}", entry)
            if name == "max-miniapp-nextjs":
                entry = action_write_overrides.get(relative, entry)
                entry = support_overrides.get(relative, entry)
                entry = analytics_overrides.get(relative, entry)
            content = exported[relative]
            data = content.encode("utf-8") if isinstance(content, str) else content
            assert hashlib.sha256(data).hexdigest() == entry["sha256"], f"{name}/{relative}"
        customized = project_export.build_runnable_export(name, {"public/omnia-inspector.js": ""})
        assert customized["public/omnia-inspector.js"] == ""
        print(f"{name}: complete export hashes and generated override passed")


if __name__ == "__main__":
    main()
