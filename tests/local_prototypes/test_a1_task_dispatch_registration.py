"""Registration and compatibility tests for the A1 task-dispatch prototype."""

from __future__ import annotations

import importlib
import json
import shutil
import sys
from types import SimpleNamespace


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
    assert set(admission.origins) == {
        "hermes_cli.kanban",
        "hermes_cli.kanban_db",
        "hermes_constants",
    }


def test_compatibility_executes_hash_check_and_rejects_changed_source(tmp_path):
    """Changing a load-bearing file must make behavioral admission fail."""
    compat = importlib.import_module("local_prototypes.a1_task_dispatch.compat")
    loaded_before = sys.modules.get("hermes_cli.kanban_db")
    exact = compat.check_compatibility()
    root = tmp_path / "release"
    (root / "hermes_cli").mkdir(parents=True)
    paths = {
        "hermes_cli.kanban": root / "hermes_cli" / "kanban.py",
        "hermes_cli.kanban_db": root / "hermes_cli" / "kanban_db.py",
        "hermes_constants": root / "hermes_constants.py",
    }
    for module_name, destination in paths.items():
        shutil.copy2(exact.origins[module_name], destination)
    paths["hermes_cli.kanban_db"].write_bytes(
        paths["hermes_cli.kanban_db"].read_bytes() + b"\n# changed\n"
    )

    admission = compat.check_compatibility(origin_resolver=paths.__getitem__)

    assert admission.ok is False
    assert admission.error == "source hash mismatch: hermes_cli.kanban_db"
    assert sys.modules.get("hermes_cli.kanban_db") is loaded_before


def test_compatibility_refuses_unknown_source_without_importing_private_module(
    tmp_path,
):
    compat = importlib.import_module("local_prototypes.a1_task_dispatch.compat")
    missing = tmp_path / "unknown-release" / "kanban.py"
    sys.modules.pop("hermes_cli.kanban", None)

    admission = compat.check_compatibility(origin_resolver=lambda _name: missing)

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
