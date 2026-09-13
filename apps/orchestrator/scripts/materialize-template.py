"""Write a complete standalone template tree to an empty destination (stdlib only)."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omnia_orchestrator.core.template_materialization import TEMPLATES, materialize_template


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", help="bundled template directory name")
    parser.add_argument("destination", type=Path, help="new or empty standalone directory")
    args = parser.parse_args()
    if Path(args.template).name != args.template or args.template in {".", ".."}:
        parser.error("template must be a directory name")
    materialize_template(TEMPLATES / args.template, args.destination)


if __name__ == "__main__":
    main()
