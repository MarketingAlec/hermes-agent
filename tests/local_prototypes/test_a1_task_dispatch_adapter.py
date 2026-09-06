"""Exact-task lifecycle tests for the release-pinned plugin adapter."""

from __future__ import annotations

import importlib
import multiprocessing
import os
import sqlite3
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


adapter = importlib.import_module("local_prototypes.a1_task_dispatch.adapter")


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(home / "kanban.db"))
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    conn = kb.connect()
    try:
        yield conn, home
    finally:
        conn.close()


def _rows(conn, table, where="", params=()):
    return [
        tuple(row)
        for row in conn.execute(f"SELECT * FROM {table} {where} ORDER BY rowid", params)
    ]


def _non_target_snapshot(conn, task_id):
    return {
        "tasks": _rows(conn, "tasks", "WHERE id != ?", (task_id,)),
        "links": _rows(
            conn,
            "task_links",
            "WHERE parent_id != ? AND child_id != ?",
            (task_id, task_id),
        ),
        "runs": _rows(conn, "task_runs", "WHERE task_id != ?", (task_id,)),
        "events": _rows(conn, "task_events", "WHERE task_id != ?", (task_id,)),
        "user_version": conn.execute("PRAGMA user_version").fetchone()[0],
    }


def _target_counts(conn, task_id):
    return tuple(
        conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (task_id,)
        ).fetchone()[0]
        for table, column in (("task_runs", "task_id"), ("task_events", "task_id"))
    )


def _hold_dispatch_lock(db_path, ready, release):
    module = importlib.import_module("local_prototypes.a1_task_dispatch.adapter")
    with module.strict_dispatch_lock(Path(db_path)) as held:
        ready.put(held)
        release.get(timeout=10)


def test_adapter_exposes_targeted_dispatch_entrypoint():
    try:
        adapter = importlib.import_module("local_prototypes.a1_task_dispatch.adapter")
    except ModuleNotFoundError:
        adapter = None

    assert adapter is not None, "targeted dispatch adapter is not implemented"
    assert callable(adapter.dispatch_target)


def test_targeted_dispatch_claims_only_selected_and_retry_is_idempotent(board):
    conn, home = board
    target = kb.create_task(conn, title="target", assignee="default")
    decoys = {}
    for status in ("todo", "ready", "review", "running", "blocked", "done", "archived"):
        task_id = kb.create_task(conn, title=f"decoy-{status}", assignee="default")
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
        decoys[status] = task_id
    failed = kb.create_task(conn, title="decoy-failed", assignee="default")
    conn.execute(
        "UPDATE tasks SET status='blocked', consecutive_failures=2, "
        "last_failure_error='fixture failure' WHERE id=?",
        (failed,),
    )
    stale = kb.create_task(conn, title="decoy-stale", assignee="default")
    conn.execute(
        "UPDATE tasks SET status='running', claim_lock='old', claim_expires=1 WHERE id=?",
        (stale,),
    )
    conn.commit()
    config_path = home / "config.yaml"
    config_path.write_text("kanban:\n  max_spawn: 8\n", encoding="utf-8")
    before_decoys = _non_target_snapshot(conn, target)
    before_config = config_path.read_bytes()
    spawned = []

    first = adapter.dispatch_target(
        target,
        dry_run=False,
        config={"max_spawn": 8},
        spawn_fn=lambda task, workspace, **kw: (
            spawned.append((task.id, workspace, kw)) or 4321
        ),
    )
    counts_after_first = _target_counts(conn, target)
    second = adapter.dispatch_target(
        target,
        dry_run=False,
        config={"max_spawn": 8},
        spawn_fn=lambda *a, **k: pytest.fail("retry spawned a duplicate worker"),
    )

    assert first.dispatched is True
    assert first.assignee == "default"
    assert spawned[0][0] == target
    assert kb.get_task(conn, target).status == "running"
    assert kb.get_task(conn, target).worker_pid == 4321
    assert second.dispatched is False
    assert second.error == f"task {target} is not ready or review"
    assert _target_counts(conn, target) == counts_after_first
    assert _non_target_snapshot(conn, target) == before_decoys
    assert config_path.read_bytes() == before_config


def test_dry_run_reads_wal_and_has_no_logical_or_config_effects(board):
    conn, home = board
    target = kb.create_task(conn, title="target", assignee="default")
    config_path = home / "config.yaml"
    config_path.write_text("kanban:\n  max_spawn: 4\n", encoding="utf-8")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("UPDATE tasks SET priority = 7 WHERE id = ?", (target,))
    conn.commit()
    wal_path = Path(os.environ["HERMES_KANBAN_DB"] + "-wal")
    assert wal_path.stat().st_size > 0
    before = _non_target_snapshot(conn, "does-not-exist")
    before_config = config_path.read_bytes()

    result = adapter.dispatch_target(
        target,
        dry_run=True,
        config={"max_spawn": 4},
        spawn_fn=lambda *a, **k: pytest.fail("dry-run spawned"),
    )

    assert result.dispatched is False
    assert result.error is None
    assert result.details["eligible"] is True
    assert _non_target_snapshot(conn, "does-not-exist") == before
    assert config_path.read_bytes() == before_config


@pytest.mark.parametrize("status", ["todo", "blocked", "done", "archived", "running"])
def test_ineligible_target_refuses_without_board_maintenance(board, status):
    conn, _ = board
    target = kb.create_task(conn, title="target", assignee="default")
    conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, target))
    conn.commit()
    before = _non_target_snapshot(conn, "does-not-exist")

    result = adapter.dispatch_target(target, dry_run=False, config={})

    assert result.error == f"task {target} is not ready or review"
    assert _non_target_snapshot(conn, "does-not-exist") == before


@pytest.mark.parametrize("status", ["ready", "review"])
def test_atomic_claim_rejects_owner_change_without_run_or_event(board, status):
    conn, _ = board
    target = kb.create_task(conn, title="target", assignee="default")
    conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, target))
    conn.commit()
    observed = "default"
    conn.execute("UPDATE tasks SET assignee = 'other' WHERE id = ?", (target,))
    conn.commit()
    before = _target_counts(conn, target)

    claimed = adapter._claim_expected(
        conn,
        task_id=target,
        source_status=status,
        expected_assignee=observed,
        ttl_seconds=None,
    )

    assert claimed is None
    assert _target_counts(conn, target) == before
    row = conn.execute(
        "SELECT status, assignee, claim_lock FROM tasks WHERE id = ?", (target,)
    ).fetchone()
    assert tuple(row) == (status, "other", None)


def test_real_cross_process_lock_contention_fails_closed(board):
    conn, _ = board
    target = kb.create_task(conn, title="target", assignee="default")
    conn.commit()
    db_path = os.environ["HERMES_KANBAN_DB"]
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    release = ctx.Queue()
    process = ctx.Process(target=_hold_dispatch_lock, args=(db_path, ready, release))
    process.start()
    try:
        assert ready.get(timeout=10) is True
        before = _non_target_snapshot(conn, "does-not-exist")
        result = adapter.dispatch_target(target, dry_run=True, config={})
        assert result.error == "dispatch lock is held"
        assert _non_target_snapshot(conn, "does-not-exist") == before
    finally:
        release.put(True)
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert process.exitcode == 0


def test_spawn_failure_uses_native_retry_cleanup_and_failure_cap(board):
    conn, _ = board
    target = kb.create_task(conn, title="target", assignee="default")

    result = adapter.dispatch_target(
        target,
        dry_run=False,
        config={"failure_limit": 1},
        spawn_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("inert failure")),
    )

    task = kb.get_task(conn, target)
    run = conn.execute(
        "SELECT status, outcome, ended_at FROM task_runs WHERE task_id = ?", (target,)
    ).fetchone()
    assert result.error == "spawn failed: inert failure"
    assert result.auto_blocked is True
    assert task.status == "blocked"
    assert task.claim_lock is None
    assert task.consecutive_failures == 1
    assert tuple(run[:2]) == ("gave_up", "gave_up")
    assert run[2] is not None


def test_native_respawn_guard_refuses_before_claim(board):
    conn, _ = board
    target = kb.create_task(conn, title="guarded", assignee="default")
    conn.execute(
        "UPDATE tasks SET last_failure_error='401 unauthorized credential' WHERE id=?",
        (target,),
    )
    conn.commit()
    before = _target_counts(conn, target)

    result = adapter.dispatch_target(target, dry_run=False, config={})

    assert result.error == "respawn guarded: blocker_auth"
    assert result.details == {"guard": "blocker_auth"}
    assert kb.get_task(conn, target).status == "ready"
    assert _target_counts(conn, target) == before


def test_review_dispatch_preserves_review_lane_and_skill(board):
    conn, _ = board
    target = kb.create_task(conn, title="review", assignee="default")
    conn.execute("UPDATE tasks SET status = 'review' WHERE id = ?", (target,))
    conn.commit()
    seen = []

    result = adapter.dispatch_target(
        target,
        dry_run=False,
        config={"review_dispatch": True},
        spawn_fn=lambda task, workspace, **kw: seen.append(task) or 91,
    )

    assert result.dispatched is True
    assert "sdlc-review" in seen[0].skills
    event = conn.execute(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='claimed' ORDER BY id DESC",
        (target,),
    ).fetchone()[0]
    assert '"source_status": "review"' in event


def test_missing_and_tombstoned_profiles_refuse(board):
    conn, home = board
    missing = kb.create_task(conn, title="missing", assignee="missing")
    profile = home / "profiles" / "worker"
    profile.mkdir(parents=True)
    tombstone = profile.parent / ".deleted" / "worker"
    tombstone.parent.mkdir()
    tombstone.write_text("deleted\n", encoding="utf-8")
    deleted = kb.create_task(conn, title="deleted", assignee="worker")

    missing_result = adapter.dispatch_target(missing, dry_run=True, config={})
    deleted_result = adapter.dispatch_target(deleted, dry_run=True, config={})

    assert missing_result.error == "assignee 'missing' is not a live Hermes profile"
    assert deleted_result.error == "assignee 'worker' is not a live Hermes profile"


def test_capacity_and_pressure_guards_refuse_before_claim(board, monkeypatch):
    conn, _ = board
    running = kb.create_task(conn, title="running", assignee="default")
    kb.claim_task(conn, running)
    target = kb.create_task(conn, title="target", assignee="default")

    board_cap = adapter.dispatch_target(target, dry_run=True, config={"max_spawn": 1})
    profile_cap = adapter.dispatch_target(
        target, dry_run=True, config={"max_in_progress_per_profile": 1}
    )
    monkeypatch.setattr(kb, "_memory_pressure_level", lambda: "critical")
    pressure = adapter.dispatch_target(target, dry_run=True, config={})

    assert board_cap.error == "board concurrency cap reached"
    assert profile_cap.error == "per-profile concurrency cap reached"
    assert pressure.error == "system memory pressure is critical"


def test_missing_board_refuses_without_creating_paths(tmp_path, monkeypatch):
    missing = tmp_path / "missing" / "kanban.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(missing))

    result = adapter.dispatch_target("t_missing", dry_run=False, config={})

    assert result.error == f"database is not initialized: {missing}"
    assert not missing.parent.exists()


def test_unknown_and_stale_selected_tasks_refuse_without_maintenance(board):
    conn, _ = board
    decoy = kb.create_task(conn, title="decoy", assignee="default")
    stale = kb.create_task(conn, title="stale", assignee="default")
    conn.execute(
        "UPDATE tasks SET status='running', claim_lock='old', claim_expires=1 "
        "WHERE id=?",
        (stale,),
    )
    conn.commit()
    before = _non_target_snapshot(conn, "does-not-exist")

    unknown = adapter.dispatch_target("t_unknown", dry_run=False, config={})
    stale_result = adapter.dispatch_target(stale, dry_run=False, config={})

    assert unknown.error == "task t_unknown not found"
    assert stale_result.error == f"task {stale} is not ready or review"
    assert kb.get_task(conn, decoy).status == "ready"
    assert _non_target_snapshot(conn, "does-not-exist") == before


def test_parent_review_gate_and_review_default_assignment_refuse_read_only(board):
    conn, _ = board
    parent = kb.create_task(conn, title="parent", assignee="default")
    child = kb.create_task(conn, title="child", assignee="default", parents=[parent])
    review = kb.create_task(conn, title="review", assignee="default")
    unowned_review = kb.create_task(conn, title="unowned-review", assignee=None)
    conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (child,))
    conn.execute(
        "UPDATE tasks SET status='review' WHERE id IN (?, ?)", (review, unowned_review)
    )
    conn.commit()
    before = _non_target_snapshot(conn, "does-not-exist")

    blocked_parent = adapter.dispatch_target(child, dry_run=True, config={})
    review_disabled = adapter.dispatch_target(
        review, dry_run=True, config={"review_dispatch": False}
    )
    no_review_default = adapter.dispatch_target(
        unowned_review,
        dry_run=True,
        config={"review_dispatch": True, "default_assignee": "default"},
    )

    assert blocked_parent.error == "unsatisfied parent dependencies"
    assert review_disabled.error == "review dispatch is disabled"
    assert no_review_default.error == "task has no assignee"
    assert _non_target_snapshot(conn, "does-not-exist") == before


def test_default_assignment_previews_without_write_then_persists_with_claim(board):
    conn, _ = board
    target = kb.create_task(conn, title="target", assignee=None)
    before = _non_target_snapshot(conn, "does-not-exist")

    preview = adapter.dispatch_target(
        target,
        dry_run=True,
        config={"default_assignee": "default"},
        spawn_fn=lambda *a, **k: pytest.fail("preview spawned"),
    )

    assert preview.error is None
    assert preview.assignee == "default"
    assert preview.details["used_default_assignee"] is True
    assert kb.get_task(conn, target).assignee is None
    assert _non_target_snapshot(conn, "does-not-exist") == before

    launched = adapter.dispatch_target(
        target,
        dry_run=False,
        config={"default_assignee": "default"},
        spawn_fn=lambda *a, **k: 29,
    )

    assert launched.dispatched is True
    task = kb.get_task(conn, target)
    assert task.assignee == "default"
    assert task.status == "running"
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM task_events WHERE task_id=? AND kind='assigned'",
            (target,),
        ).fetchone()[0]
        == 1
    )


def test_lock_sidecar_open_failure_refuses_without_board_write(board, monkeypatch):
    conn, _ = board
    target = kb.create_task(conn, title="target", assignee="default")
    conn.commit()
    before = _non_target_snapshot(conn, "does-not-exist")
    original_open = Path.open

    def fail_lock(path, *args, **kwargs):
        if path.name.endswith(".dispatch.lock"):
            raise PermissionError("denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_lock)
    result = adapter.dispatch_target(target, dry_run=False, config={})

    assert result.error == "dispatch lock unavailable"
    assert _non_target_snapshot(conn, "does-not-exist") == before


def test_outdated_schema_refuses_without_migration(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE tasks (id TEXT, status TEXT, assignee TEXT, claim_lock TEXT)"
        )
        conn.execute("INSERT INTO tasks VALUES ('t_old', 'ready', 'default', NULL)")
    before = path.read_bytes()
    monkeypatch.setenv("HERMES_KANBAN_DB", str(path))

    result = adapter.dispatch_target("t_old", dry_run=False, config={})

    assert result.error == "database schema is not initialized/current"
    assert path.read_bytes() == before
    with sqlite3.connect(path) as conn:
        assert [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ] == ["tasks"]


def test_real_other_board_consumes_host_capacity(board, monkeypatch):
    conn, _ = board
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    kb.create_board("second")
    with kb.connect(board="second") as other:
        running = kb.create_task(other, title="busy", assignee="default")
        assert kb.claim_task(other, running) is not None
    target = kb.create_task(conn, title="target", assignee="default")

    result = adapter.dispatch_target(
        target,
        dry_run=True,
        config={"max_in_progress": 1},
        board="default",
    )

    assert result.error == "host concurrency cap reached"


def test_missing_registered_other_board_fails_closed(board, monkeypatch):
    conn, _ = board
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    kb.create_board("vanished")
    missing_db = kb.kanban_db_path(board="vanished")
    missing_db.unlink()
    target = kb.create_task(conn, title="target", assignee="default")

    result = adapter.dispatch_target(
        target,
        dry_run=True,
        config={"max_in_progress": 2},
        board="default",
    )

    assert result.error == "cannot verify host concurrency cap"
    assert not missing_db.exists()
