"""Portable macOS/Linux smoke runner for the public plugin command."""

from __future__ import annotations

import json
import platform
import sys
import tempfile
from pathlib import Path

from .harness import (
    initialize_board,
    install_inert_neighbor,
    install_plugin,
    logical_db_snapshot,
    make_fixture,
    run_cli,
    tree_snapshot,
    write_config,
)


def main() -> int:
    if sys.version_info[:2] != (3, 13):
        print("REFUSED: portable runner requires Python 3.13", file=sys.stderr)
        return 2
    if sys.platform not in {"darwin", "linux"}:
        print("REFUSED: portable runner supports macOS and Linux only", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="a1-task-dispatch-") as raw:
        fixture = make_fixture(Path(raw))
        install_plugin(fixture)
        install_inert_neighbor(fixture)
        write_config(fixture, enabled=("a1_task_dispatch", "inert_neighbor"))
        task_id = initialize_board(fixture)
        tree_before = tree_snapshot(fixture.hermes_home)
        db_before = logical_db_snapshot(fixture.db_path)
        result = run_cli(
            fixture,
            "a1-task-dispatch",
            "--task-id",
            task_id,
            "--dry-run",
            "--json",
        )
        if result.returncode != 0:
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            return result.returncode
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        if not payload.get("ok") or payload.get("task_id") != task_id:
            print(f"REFUSED: unexpected payload: {payload}", file=sys.stderr)
            return 2
        if logical_db_snapshot(fixture.db_path) != db_before:
            print("REFUSED: dry-run changed logical database content", file=sys.stderr)
            return 2
        tree_after = tree_snapshot(fixture.hermes_home)
        changed_existing = {
            path
            for path in tree_before.keys() & tree_after.keys()
            if tree_before[path] != tree_after[path]
        }
        protected = {"config.yaml", "SOUL.md"}
        if changed_existing & protected:
            print("REFUSED: dry-run changed protected startup files", file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "ok": True,
                    "platform": platform.platform(),
                    "python": platform.python_version(),
                    "task_id": task_id,
                    "new_paths": sorted(tree_after.keys() - tree_before.keys()),
                    "changed_existing": sorted(changed_existing),
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
