"""Registration and compatibility tests for the A1 task-dispatch prototype."""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from local_prototypes.a1_task_dispatch.testing.manifest_closure import (
    release_manifest_closure,
)
from local_prototypes.a1_task_dispatch.testing.runtime_manifest import (
    release_runtime_sources,
)


class _RecordingContext:
    def __init__(self) -> None:
        self.commands: list[dict[str, object]] = []

    def register_cli_command(self, **command: object) -> None:
        self.commands.append(command)


def test_registration_is_inert_and_exposes_only_the_namespaced_command():
    """Importing/registering must not load dispatcher internals or add tools/hooks."""
    before = set(sys.modules)
    try:
        plugin = importlib.import_module("local_prototypes.a1_task_dispatch")
    except ModuleNotFoundError:
        plugin = None

    assert plugin is not None, "prototype plugin package is not implemented"
    ctx = _RecordingContext()
    plugin.register(ctx)

    assert [entry["name"] for entry in ctx.commands] == ["a1-task-dispatch"]
    assert "hermes_cli.kanban_db" not in set(sys.modules) - before
    assert "hermes_cli.kanban" not in set(sys.modules) - before


def test_compatibility_accepts_the_exact_loaded_release_sources():
    compat = importlib.import_module("local_prototypes.a1_task_dispatch.compat")

    admission = compat.check_compatibility()

    assert admission.ok is True
    assert admission.error is None
    assert {
        "hermes_cli.kanban",
        "hermes_cli.kanban_db",
        "hermes_constants",
        "gateway.lifecycle_ledger",
        "gateway.memory_status",
    } <= set(admission.origins)


def test_compatibility_manifest_covers_static_release_import_closure():
    """Every conservatively walked import-time source remains pinned."""
    compat = importlib.import_module("local_prototypes.a1_task_dispatch.compat")
    repo_root = Path(__file__).resolve().parents[2]

    static_sources = release_manifest_closure(repo_root)

    assert not (set(static_sources) - set(compat.EXPECTED_HASHES))
    assert all(
        compat.SOURCE_PATHS[module_name] == source_path
        for module_name, source_path in static_sources.items()
    )


def test_compatibility_manifest_covers_isolated_runtime_source_inventory(tmp_path):
    """Any release source executed by fixed-seed initialization must be pinned."""
    compat = importlib.import_module("local_prototypes.a1_task_dispatch.compat")
    repo_root = Path(__file__).resolve().parents[2]

    runtime_sources = release_runtime_sources(repo_root, tmp_path / "runtime-inventory")

    missing = set(runtime_sources) - set(compat.EXPECTED_HASHES)
    mismatched = {
        module_name: (compat.SOURCE_PATHS.get(module_name), source_path)
        for module_name, source_path in runtime_sources.items()
        if compat.SOURCE_PATHS.get(module_name) != source_path
    }
    assert not missing, f"executed release sources are unpinned: {sorted(missing)}"
    assert not mismatched, f"executed release source paths differ: {mismatched}"


def test_compatibility_manifest_covers_lazy_claim_hook_runtime_sources(tmp_path):
    """Release dependencies first loaded by the ready-claim hook must be pinned."""
    compat = importlib.import_module("local_prototypes.a1_task_dispatch.compat")
    repo_root = Path(__file__).resolve().parents[2]

    runtime_sources = release_runtime_sources(
        repo_root, tmp_path / "claim-hook-inventory", include_claim_hook=True
    )

    missing = set(runtime_sources) - set(compat.EXPECTED_HASHES)
    mismatched = {
        module_name: (compat.SOURCE_PATHS.get(module_name), source_path)
        for module_name, source_path in runtime_sources.items()
        if compat.SOURCE_PATHS.get(module_name) != source_path
    }
    assert not missing, f"claim-hook release sources are unpinned: {sorted(missing)}"
    assert not mismatched, f"claim-hook source paths differ: {mismatched}"


def test_compatibility_executes_hash_check_and_rejects_changed_source(tmp_path):
    """Changing a load-bearing file must make behavioral admission fail."""
    compat = importlib.import_module("local_prototypes.a1_task_dispatch.compat")
    loaded_before = sys.modules.get("hermes_cli.kanban_db")
    exact = compat.check_compatibility()
    root = tmp_path / "release"
    paths = {}
    for module_name, source in exact.origins.items():
        destination = root / compat.SOURCE_PATHS[module_name]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        paths[module_name] = destination
    paths["hermes_cli.kanban_db"].write_bytes(
        paths["hermes_cli.kanban_db"].read_bytes() + b"\n# changed\n"
    )

    admission = compat.check_compatibility(origin_resolver=paths.__getitem__)

    assert admission.ok is False
    assert admission.error == "source hash mismatch: hermes_cli.kanban_db"
    assert sys.modules.get("hermes_cli.kanban_db") is loaded_before


def test_compatibility_refuses_an_extended_manifest_split_across_checkouts(tmp_path):
    compat = importlib.import_module("local_prototypes.a1_task_dispatch.compat")
    exact = compat.check_compatibility()
    coherent_root = tmp_path / "release"
    split_root = tmp_path / "other-release"
    paths = {}
    for module_name, source in exact.origins.items():
        destination = coherent_root / compat.SOURCE_PATHS[module_name]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        paths[module_name] = destination
    split_memory_status = split_root / "gateway" / "memory_status.py"
    split_memory_status.parent.mkdir(parents=True)
    shutil.copy2(Path("gateway/memory_status.py"), split_memory_status)
    paths["gateway.memory_status"] = split_memory_status

    admission = compat.check_compatibility(origin_resolver=paths.__getitem__)

    assert admission.ok is False
    assert admission.error == "source origin mismatch: gateway.memory_status"


def test_compatibility_resolution_does_not_import_private_modules():
    script = """
import json
import sys
before = set(sys.modules)
from local_prototypes.a1_task_dispatch.compat import check_compatibility
admission = check_compatibility()
private_roots = {
    'agent', 'gateway', 'hermes_cli', 'hermes_constants', 'hermes_state', 'toolsets'
}
added = sorted(
    name for name in set(sys.modules) - before
    if name.partition('.')[0] in private_roots
)
print(json.dumps({'ok': admission.ok, 'added': added}))
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["added"] == []


def test_compatibility_refuses_unknown_source_without_importing_private_module(
    tmp_path,
):
    compat = importlib.import_module("local_prototypes.a1_task_dispatch.compat")
    exact = compat.check_compatibility()
    missing = tmp_path / "unknown-release" / "kanban.py"
    sys.modules.pop("hermes_cli.kanban", None)

    admission = compat.check_compatibility(
        origin_resolver=lambda name: (
            missing if name == "hermes_cli.kanban" else exact.origins[name]
        )
    )

    assert admission.ok is False
    assert admission.error is not None
    assert admission.error.startswith("source unavailable: hermes_cli.kanban:")
    assert "hermes_cli.kanban" not in sys.modules


def test_invalid_task_id_refuses_without_loading_private_dispatcher(capsys):
    plugin = importlib.import_module("local_prototypes.a1_task_dispatch")
    sys.modules.pop("hermes_cli.kanban_db", None)

    rc = plugin._handle_command(
        SimpleNamespace(task_id="bad id", dry_run=True, json=True)
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert payload["error"] == "task id must be a non-empty token"
    assert "hermes_cli.kanban_db" not in sys.modules


def test_compatibility_refusal_happens_before_private_import_or_board_effects(
    tmp_path,
    monkeypatch,
    capsys,
):
    plugin = importlib.import_module("local_prototypes.a1_task_dispatch")
    compat = importlib.import_module("local_prototypes.a1_task_dispatch.compat")
    board = tmp_path / "must-not-exist.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(board))
    monkeypatch.setattr(
        compat,
        "check_compatibility",
        lambda: compat.Admission(
            False, "source hash mismatch: hermes_cli.kanban_db", {}
        ),
    )
    sys.modules.pop("hermes_cli.kanban_db", None)

    rc = plugin._handle_command(
        SimpleNamespace(task_id="t_exact", dry_run=False, json=True)
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert payload["error"] == "source hash mismatch: hermes_cli.kanban_db"
    assert not board.exists()
    assert "hermes_cli.kanban_db" not in sys.modules
