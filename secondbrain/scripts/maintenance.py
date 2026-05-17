"""Nightly maintenance runner: docs sync -> compile -> lint once per day."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone

from config import COMPILE_AFTER_HOUR, ROOT_DIR, now_iso
from utils import append_log_block, ensure_structure, list_wiki_articles, load_maintenance_state, save_maintenance_state


def _today_local() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout


def _extract_metric(output: str, key: str, fallback: str = "n/a") -> str:
    for line in output.splitlines():
        if key in line:
            return line.strip()
    return fallback


def main() -> int:
    parser = argparse.ArgumentParser(description="Run nightly secondbrain maintenance")
    parser.add_argument("--force", action="store_true", help="Run even if already executed today")
    args = parser.parse_args()

    ensure_structure()
    now = datetime.now(timezone.utc).astimezone()
    today = _today_local()
    state = load_maintenance_state()
    before_count = len(list_wiki_articles())
    if not args.force:
        if now.hour < COMPILE_AFTER_HOUR:
            print(f"Skip: before {COMPILE_AFTER_HOUR}:00")
            return 0
        if state.get("last_maintenance_date") == today:
            print("Skip: already executed today")
            return 0

    steps = [
        [sys.executable, "scripts/sync_project_docs.py"],
        [sys.executable, "scripts/compile.py"],
        [sys.executable, "scripts/repair_backlinks.py"],
        [sys.executable, "scripts/lint.py", "--structural-only"],
    ]
    outputs: list[str] = []
    step_outputs: dict[str, str] = {}
    for cmd in steps:
        code, out = _run(cmd)
        outputs.append(f"$ {' '.join(cmd)}\n{out.strip()}\n")
        step_outputs[" ".join(cmd[1:])] = out.strip()
        if code != 0:
            print("\n".join(outputs))
            print("Maintenance failed.")
            return 1

    after_count = len(list_wiki_articles())
    compile_out = step_outputs.get("scripts/compile.py", "")
    compile_metrics = _extract_metric(
        compile_out,
        "Compile metrics:",
        fallback="Compile metrics: n/a (no changed daily logs)",
    )
    quality_metrics = _extract_metric(compile_out, "Quality enrichment:", fallback="Quality enrichment: n/a")
    docs_metrics = _extract_metric(step_outputs.get("scripts/sync_project_docs.py", ""), "Docs changed:", fallback="Docs changed: 0")
    if "No docs changes detected." in step_outputs.get("scripts/sync_project_docs.py", ""):
        docs_metrics = "Docs changed: 0"
    backlink_metrics = _extract_metric(step_outputs.get("scripts/repair_backlinks.py", ""), "Backlink repair done.")

    state["last_maintenance_date"] = today
    save_maintenance_state(state)
    append_log_block(
        f"[{now_iso()}] maintenance | nightly",
        [
            f"Wiki totals: before={before_count}, after={after_count}, delta={after_count - before_count}",
            docs_metrics,
            quality_metrics,
            compile_metrics,
            backlink_metrics,
            "Structural lint: completed",
        ],
    )
    print("\n".join(outputs))
    print(
        "Maintenance summary: "
        f"wiki_before={before_count}, wiki_after={after_count}, delta={after_count - before_count}; "
        f"{docs_metrics}; {quality_metrics}; {backlink_metrics}"
    )
    print("Maintenance completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

