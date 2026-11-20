"""Schedule the complete API file suite; timings never filter test membership."""

import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath


def partition(names, count, weights=None):
    if type(count) is not int or not 1 <= count <= 64:
        raise ValueError("shard count must be between 1 and 64")
    ordered = sorted(names)
    if len(ordered) != len(set(ordered)):
        raise ValueError("duplicate test file")
    if weights is None:
        # Keep the existing schedule when no measured timing file is available.
        return [
            [name for index, name in enumerate(ordered, 1) if index % count == shard]
            for shard in range(count)
        ]
    if not isinstance(weights, dict) or set(weights) - set(ordered):
        raise ValueError("weights must refer only to current test files")
    if any(not valid_seconds(value) for value in weights.values()):
        raise ValueError("weights must be finite nonnegative seconds")
    # Unknown/changed sources get the largest current measured weight, so they
    # are spread rather than silently treated as cheap. They are never omitted.
    fallback = max(weights.values(), default=1.0) or 1.0
    duration = {name: weights.get(name, fallback) for name in ordered}
    groups = [[] for _ in range(count)]
    totals = [0.0] * count
    for name in sorted(ordered, key=lambda name: (-duration[name], name)):
        shard = min(range(count), key=lambda index: (totals[index], len(groups[index]), index))
        groups[shard].append(name)
        totals[shard] += duration[name]
    return [sorted(group) for group in groups]


def valid_seconds(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def load_weights(path, root, names):
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("timing data must be a regular file")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("files"), dict):
        raise ValueError("invalid timing data schema")
    for name, entry in data["files"].items():
        source = PurePosixPath(name)
        if (
            len(source.parts) != 2 or source.parts[0] != "tests"
            or not source.name.startswith("test_") or not source.name.endswith(".py")
            or "\\" in name or source.as_posix() != name
            or not isinstance(entry, dict) or set(entry) != {"sha256", "seconds"}
            or not isinstance(entry["sha256"], str) or len(entry["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in entry["sha256"])
            or not valid_seconds(entry["seconds"])
        ):
            raise ValueError("invalid timing source or duration")
    weights = {}
    for name in names:
        entry = data["files"].get(name)
        if entry is not None:
            raw_lf = (root / name).read_bytes().replace(b"\r\n", b"\n")
            if hashlib.sha256(raw_lf).hexdigest() == entry["sha256"]:
                weights[name] = entry["seconds"]
    return weights


def verify_coverage(names, groups, required):
    flattened = [name for group in groups for name in group]
    if not all(groups) or sorted(flattened) != sorted(names) or len(flattened) != len(set(flattened)):
        raise ValueError("shards must contain every current test file exactly once")
    if len(required) != len(set(required)) or set(required) - set(flattened):
        raise ValueError("required release-critical file missing or duplicated")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards", type=int, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--shard", type=int)
    mode.add_argument("--verify-coverage", action="store_true")
    parser.add_argument("--require-files", nargs="*", default=[])
    args = parser.parse_args()
    if args.shard is not None and not 1 <= args.shard <= args.shards:
        parser.error("shard must be between 1 and --shards")
    if args.require_files and not args.verify_coverage:
        parser.error("--require-files is only valid with --verify-coverage")
    files = sorted(Path("tests").glob("test_*.py"))
    if not files or any(path.is_symlink() or not path.is_file() for path in files):
        parser.error("expected nonempty regular API test files")
    names = [path.as_posix() for path in files]
    try:
        weights = load_weights(Path(__file__).with_name("api-test-timings.json"), Path.cwd(), names)
        groups = partition(names, args.shards, weights)
        verify_coverage(names, groups, args.require_files)
    except ValueError as exc:
        parser.error(str(exc))
    if args.verify_coverage:
        print(json.dumps({"status": "complete_api_shard_coverage", "files": len(names),
                          "shards": args.shards, "required_files": len(args.require_files)}))
    else:
        print("\n".join(groups[args.shard - 1]))


if __name__ == "__main__":
    main()
