"""Refresh the pinned ReUI metadata snapshot without executing upstream code.

Usage from ``apps/api``::

    uv run python scripts/sync_reui_catalog.py

The commit pin is intentionally explicit. Review and update it before each
refresh; the script verifies the upstream MIT license and writes deterministic
JSON plus the exact license notice used by the snapshot.
"""

from __future__ import annotations

import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

REPOSITORY = "keenthemes/reui"
COMMIT = "0daf79dff3ebe0ede7fa05bedcaefeaac93a8949"
COMMIT_DATE = "2026-08-20T05:19:32Z"
CATALOG_PREFIX = "registry-reui/bases/base/components/"
API_TREE_URL = f"https://api.github.com/repos/{REPOSITORY}/git/trees/{COMMIT}?recursive=1"
RAW_ROOT = f"https://raw.githubusercontent.com/{REPOSITORY}/{COMMIT}"
REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO_ROOT / "data" / "reui_catalog.json"
LICENSE_PATH = REPO_ROOT / "data" / "reui" / "LICENSE.md"


def _request(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "omnia-reui-sync/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return cast(bytes, response.read())


def _json(url: str) -> Any:
    return json.loads(_request(url).decode("utf-8"))


def _meta_paths() -> tuple[str, ...]:
    tree = _json(API_TREE_URL)
    if not isinstance(tree, dict) or tree.get("truncated"):
        raise RuntimeError("GitHub returned a missing or truncated ReUI tree")
    paths = {
        item["path"]
        for item in tree.get("tree", ())
        if isinstance(item, dict)
        and item.get("type") == "blob"
        and isinstance(item.get("path"), str)
        and item["path"].startswith(CATALOG_PREFIX)
        and item["path"].endswith("/meta.json")
    }
    if not paths:
        raise RuntimeError("Pinned ReUI tree contains no component metadata")
    return tuple(sorted(paths))


def _load_meta(path: str) -> tuple[str, Any]:
    return path, _json(f"{RAW_ROOT}/{path}")


def build_catalog() -> tuple[dict[str, Any], str]:
    paths = _meta_paths()
    with ThreadPoolExecutor(max_workers=8) as pool:
        documents = tuple(pool.map(_load_meta, paths))

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path, raw_items in documents:
        if not isinstance(raw_items, list):
            raise RuntimeError(f"Unexpected ReUI metadata shape: {path}")
        category = path.removeprefix(CATALOG_PREFIX).split("/", maxsplit=1)[0]
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            name = raw.get("name")
            title = raw.get("title")
            description = raw.get("description")
            if not all(isinstance(value, str) and value for value in (name, title, description)):
                continue
            pattern_id = f"{category}/{name}"
            if pattern_id in seen:
                raise RuntimeError(f"Duplicate ReUI pattern id: {pattern_id}")
            seen.add(pattern_id)
            items.append(
                {
                    "id": pattern_id,
                    "category": category,
                    "name": name,
                    "title": title,
                    "description": description,
                    "order": raw.get("order"),
                    "source_url": (
                        f"https://github.com/{REPOSITORY}/blob/{COMMIT}/"
                        f"{CATALOG_PREFIX}{category}/{name}.tsx"
                    ),
                }
            )
    items.sort(key=lambda item: (item["category"], item["order"] or 0, item["name"]))
    license_text = _request(f"{RAW_ROOT}/LICENSE.md").decode("utf-8")
    if "MIT License" not in license_text or "Copyright (c) 2025 Keenthemes Inc" not in license_text:
        raise RuntimeError("Pinned ReUI license is not the reviewed MIT notice")
    catalog = {
        "schema_version": 1,
        "source": {
            "repository": f"https://github.com/{REPOSITORY}",
            "commit": COMMIT,
            "commit_date": COMMIT_DATE,
            "license": "MIT",
            "license_file": "reui/LICENSE.md",
            "catalog_path": CATALOG_PREFIX.rstrip("/"),
            "note": "Metadata only. Upstream executable source is not vendored or run.",
        },
        "item_count": len(items),
        "items": items,
    }
    return catalog, license_text


def main() -> None:
    catalog, license_text = build_catalog()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    LICENSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    LICENSE_PATH.write_text(license_text.rstrip() + "\n", encoding="utf-8")
    print(f"wrote {catalog['item_count']} ReUI patterns to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
