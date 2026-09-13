"""Verify mounted container-template exports from the installed API, without network."""

import argparse
import hashlib
import json
from pathlib import Path

from omnia_api.services import project_export

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("golden", type=Path)
    parser.add_argument("readme_overrides", type=Path)
    args = parser.parse_args()
    golden = json.loads(args.golden.read_text(encoding="utf-8"))["templates"]
    overrides = json.loads(args.readme_overrides.read_text(encoding="utf-8"))
    for name, complete_tree in golden.items():
        omissions = _EXPORT_OMISSIONS[name]
        assert omissions <= complete_tree.keys(), f"unknown frozen export omissions: {name}"
        expected = {path: entry for path, entry in complete_tree.items() if path not in omissions}
        exported = project_export.build_runnable_export(name, {})
        exported.pop("README.omnia.md", None)
        assert exported.keys() == expected.keys(), f"incomplete standalone export: {name}"
        for relative, entry in expected.items():
            entry = overrides.get(f"{name}/{relative}", entry)
            content = exported[relative]
            data = content.encode("utf-8") if isinstance(content, str) else content
            assert hashlib.sha256(data).hexdigest() == entry["sha256"], f"{name}/{relative}"
        customized = project_export.build_runnable_export(name, {"public/omnia-inspector.js": ""})
        assert customized["public/omnia-inspector.js"] == ""
        print(f"{name}: complete export hashes and generated override passed")


if __name__ == "__main__":
    main()
