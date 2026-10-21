"""Print one exhaustive file shard; scheduling never filters the API suite."""

import argparse
from pathlib import Path


def partition(names, count):
    if type(count) is not int or not 1 <= count <= 64:
        raise ValueError("shard count must be between 1 and 64")
    ordered = sorted(names)
    if len(ordered) != len(set(ordered)):
        raise ValueError("duplicate test file")
    # Preserve the old NR % 3 groups as paired shards (1+4, 2+5, 3+6).
    # There is no measured per-file history yet; do not invent duration weights.
    return [
        [name for index, name in enumerate(ordered, 1) if index % count == shard]
        for shard in range(count)
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--shard", type=int, required=True)
    args = parser.parse_args()
    if not 1 <= args.shard <= args.shards:
        parser.error("shard must be between 1 and --shards")
    files = sorted(Path("tests").glob("test_*.py"))
    if not files or any(path.is_symlink() or not path.is_file() for path in files):
        parser.error("expected nonempty regular API test files")
    groups = partition([path.as_posix() for path in files], args.shards)
    if not groups[args.shard - 1]:
        parser.error("empty shard; review shard count instead of running all tests accidentally")
    print("\n".join(groups[args.shard - 1]))


if __name__ == "__main__":
    main()
