"""Behavioral coverage for exact-task dispatcher selection."""

from __future__ import annotations

import argparse
import sqlite3

import pytest

from hermes_cli import kanban as cli
from hermes_cli import kanban_db as kb


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(home / "kanban.db"))
    (home / "profiles" / "default").mkdir(parents=True)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    with kb.connect_closing() as conn:
        yield conn


def _snapshot(conn):
    return {
        table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
        for table in ("tasks", "task_runs", "task_events")
    }


def test_parser_accepts_exact_task_selector():
    root = argparse.ArgumentParser()
    args = cli.build_parser(root.add_subparsers()).parse_args(
        ["dispatch", "--task-id", "t_exact", "--dry-run"]
    )
    assert args.task_id == "t_exact"


def test_targeted_dispatch_spawns_only_selected_and_skips_maintenance(board, monkeypatch):
    target = kb.create_task(board, title="target", assignee="default")
    decoy = kb.create_task(board, title="decoy", assignee="default")
    forbidden = [
        "reap_worker_zombies", "release_stale_claims", "reconcile_orphaned_running",
        "detect_stale_running", "detect_crashed_workers", "enforce_max_runtime",
        "recompute_ready", "_maybe_checkpoint_wal", "_fire_dispatch_tick_hook",
    ]
    for name in forbidden:
        monkeypatch.setattr(kb, name, lambda *a, _name=name, **k: pytest.fail(_name))
    decoy_before = {
        table: [tuple(r) for r in board.execute(
            f"SELECT * FROM {table} WHERE task_id = ? ORDER BY 1", (decoy,)
        )] if table != "tasks" else [tuple(board.execute(
            "SELECT * FROM tasks WHERE id = ?", (decoy,)
        ).fetchone())]
        for table in ("tasks", "task_runs", "task_events")
    }
    spawned = []
    result = kb.dispatch_once(
        board, task_id=target,
        spawn_fn=lambda task, workspace, **kw: spawned.append(task.id) or 4321,
    )
    assert spawned == [target]
    assert [item[0] for item in result.spawned] == [target]
    assert kb.get_task(board, target).status == "running"
    assert kb.get_task(board, decoy).status == "ready"
    decoy_after = {
        table: [tuple(r) for r in board.execute(
            f"SELECT * FROM {table} WHERE task_id = ? ORDER BY 1", (decoy,)
        )] if table != "tasks" else [tuple(board.execute(
            "SELECT * FROM tasks WHERE id = ?", (decoy,)
        ).fetchone())]
        for table in ("tasks", "task_runs", "task_events")
    }
    assert decoy_after == decoy_before


def test_targeted_dry_run_is_read_only_and_checks_parents(board):
    parent = kb.create_task(board, title="parent", assignee="default")
    child = kb.create_task(board, title="child", assignee=None, parents=[parent])
    board.execute("UPDATE tasks SET status = 'ready' WHERE id = ?", (child,))
    board.commit()
    before = _snapshot(board)
    result = kb.dispatch_once(
        board, task_id=child, dry_run=True, default_assignee="default",
        spawn_fn=lambda *a, **k: pytest.fail("dry-run spawned"),
    )
    assert result.spawned == []
    assert result.target_error == "unsatisfied parent dependencies"
    assert _snapshot(board) == before


def test_targeted_default_assignment_dry_run_is_not_persisted(board):
    target = kb.create_task(board, title="target", assignee=None)
    before = _snapshot(board)
    result = kb.dispatch_once(
        board, task_id=target, dry_run=True, default_assignee="default",
    )
    assert result.spawned == [(target, "default", "")]
    assert result.auto_assigned_default == [target]
    assert _snapshot(board) == before


@pytest.mark.parametrize("task_id", ["", "missing", "bad id"])
def test_targeted_dispatch_fails_closed_for_invalid_selection(board, task_id):
    decoy = kb.create_task(board, title="decoy", assignee="default")
    result = kb.dispatch_once(board, task_id=task_id, dry_run=True)
    assert result.spawned == []
    assert result.target_error
    assert kb.get_task(board, decoy).status == "ready"


def test_targeted_dispatch_refuses_lock_path_resolution_failure(board, monkeypatch):
    target = kb.create_task(board, title="target", assignee="default")
    monkeypatch.setattr(kb, "kanban_db_path", lambda **kw: (_ for _ in ()).throw(ValueError("bad path")))
    result = kb.dispatch_once(board, task_id=target)
    assert result.target_error == "dispatch lock path unavailable"
    assert kb.get_task(board, target).status == "ready"


def test_targeted_dispatch_refuses_lock_contention(board):
    target = kb.create_task(board, title="target", assignee="default")
    with kb._dispatch_tick_lock(kb.kanban_db_path()) as held:
        assert held
        result = kb.dispatch_once(board, task_id=target)
    assert result.skipped_locked is True
    assert result.spawned == []
    assert kb.get_task(board, target).status == "ready"


@pytest.mark.parametrize("status", ["todo", "blocked", "done", "archived", "running"])
def test_targeted_dispatch_refuses_ineligible_status(board, status):
    target = kb.create_task(board, title="target", assignee="default")
    board.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, target))
    board.commit()
    result = kb.dispatch_once(board, task_id=target, dry_run=True)
    assert result.spawned == []
    assert result.target_error == f"task {target} is not ready or review"


def test_targeted_review_obeys_review_lane_and_retry_does_not_duplicate(board):
    target = kb.create_task(board, title="review", assignee="default")
    board.execute("UPDATE tasks SET status = 'review' WHERE id = ?", (target,))
    board.commit()
    spawned = []
    first = kb.dispatch_once(
        board, task_id=target,
        spawn_fn=lambda task, workspace, **kw: spawned.append((task.id, task.skills)) or 77,
    )
    second = kb.dispatch_once(
        board, task_id=target,
        spawn_fn=lambda *a, **k: pytest.fail("duplicate spawn"),
    )
    assert first.spawned[0][0] == target
    assert "sdlc-review" in spawned[0][1]
    assert second.spawned == []
    assert second.target_error


def test_targeted_dispatch_obeys_capacity_caps(board):
    running = kb.create_task(board, title="running", assignee="default")
    kb.claim_task(board, running)
    target = kb.create_task(board, title="target", assignee="default")
    result = kb.dispatch_once(
        board, task_id=target, dry_run=True,
        max_in_progress_per_profile=1,
    )
    assert result.spawned == []
    assert result.target_error == "per-profile concurrency cap reached"


def test_targeted_dispatch_obeys_board_and_host_caps(board, monkeypatch):
    running = kb.create_task(board, title="running", assignee="default")
    kb.claim_task(board, running)
    target = kb.create_task(board, title="target", assignee="default")
    board_cap = kb.dispatch_once(board, task_id=target, dry_run=True, max_spawn=1)
    assert board_cap.target_error == "board concurrency cap reached"

    monkeypatch.setattr(kb, "count_running_tasks_other_boards_readonly", lambda board=None: 2)
    host_cap = kb.dispatch_once(board, task_id=target, dry_run=True, max_in_progress=3)
    assert host_cap.target_error == "host concurrency cap reached"


def test_cli_targeted_dry_run_refuses_missing_db_without_creating_it(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "missing" / "kanban.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(missing))
    args = argparse.Namespace(
        task_id="t_exact", dry_run=True, max=None,
        failure_limit=kb.DEFAULT_SPAWN_FAILURE_LIMIT, json=False,
    )
    assert cli._cmd_dispatch(args) != 0
    assert not missing.exists()
    assert "not initialized" in capsys.readouterr().err


def test_cli_targeted_real_dispatch_refuses_outdated_schema_without_migration(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE tasks (id TEXT, status TEXT, assignee TEXT, claim_lock TEXT)")
    conn.execute("INSERT INTO tasks VALUES ('t_old', 'ready', 'default', NULL)")
    conn.commit()
    conn.close()
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    args = argparse.Namespace(
        task_id="t_old", dry_run=False, max=None,
        failure_limit=kb.DEFAULT_SPAWN_FAILURE_LIMIT, json=False,
    )
    assert cli._cmd_dispatch(args) == 2
    assert "schema is not initialized/current" in capsys.readouterr().err
    with sqlite3.connect(db_path) as check:
        assert [r[0] for r in check.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )] == ["tasks"]


def test_cli_targeted_dry_run_does_not_initialize_config_or_write_db(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    db_path = home / "kanban.db"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    with kb.connect_closing() as conn:
        target = kb.create_task(conn, title="target", assignee=None)
    before = None
    with sqlite3.connect(db_path) as check:
        check.row_factory = sqlite3.Row
        before = _snapshot(check)
    args = argparse.Namespace(
        task_id=target, dry_run=True, max=None,
        failure_limit=kb.DEFAULT_SPAWN_FAILURE_LIMIT, json=False,
    )
    assert cli._cmd_dispatch(args) == 2
    with sqlite3.connect(db_path) as check:
        check.row_factory = sqlite3.Row
        assert _snapshot(check) == before
    assert not (home / "config.yaml").exists()


def test_cli_targeted_dry_run_reads_committed_uncheckpointed_wal(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    db_path = home / "kanban.db"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    writer = kb.connect()
    try:
        target = kb.create_task(writer, title="target", assignee="default")
        writer.execute("UPDATE tasks SET status = 'blocked' WHERE id = ?", (target,))
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.execute("UPDATE tasks SET status = 'ready' WHERE id = ?", (target,))
        writer.commit()
        assert (db_path.parent / f"{db_path.name}-wal").stat().st_size > 0
        args = argparse.Namespace(
            task_id=target, dry_run=True, max=None,
            failure_limit=kb.DEFAULT_SPAWN_FAILURE_LIMIT, json=False,
        )
        assert cli._cmd_dispatch(args) == 0
    finally:
        writer.close()


def test_targeted_host_cap_refuses_unreadable_other_board(board, tmp_path, monkeypatch):
    target = kb.create_task(board, title="target", assignee="default")
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_text("not sqlite", encoding="utf-8")
    original_path = kb.kanban_db_path
    monkeypatch.setattr(kb, "list_boards", lambda include_archived=False: [{"slug": "other"}])
    monkeypatch.setattr(
        kb, "kanban_db_path",
        lambda board=None: corrupt if board == "other" else original_path(board=board),
    )
    result = kb.dispatch_once(board, task_id=target, dry_run=True, max_in_progress=5)
    assert result.spawned == []
    assert result.target_error == "cannot verify host concurrency cap"
