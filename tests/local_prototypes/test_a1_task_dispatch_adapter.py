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

_MIXED_STATE_CAPACITY = {
    "max_spawn": 64,
    "max_in_progress": 64,
    "max_in_progress_per_profile": 64,
}


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


@pytest.fixture
def mixed_board(board):
    conn, home = board
    _seed_mixed_state_decoys(conn)
    (home / "config.yaml").write_text(
        "kanban:\n  max_spawn: 64\nfixture:\n  sentinel: mixed-state\n",
        encoding="utf-8",
    )
    return conn, home


def _rows(conn, table):
    return [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def _rows_without_tasks(conn, table, task_column, target_ids):
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    task_index = columns.index(task_column)
    return [row for row in _rows(conn, table) if row[task_index] not in target_ids]


def _non_target_snapshot(conn, *target_ids):
    excluded = frozenset(target_ids)
    links = _rows(conn, "task_links")
    link_columns = [row[1] for row in conn.execute("PRAGMA table_info(task_links)")]
    parent_index = link_columns.index("parent_id")
    child_index = link_columns.index("child_id")
    return {
        "tasks": _rows_without_tasks(conn, "tasks", "id", excluded),
        "links": [
            row
            for row in links
            if row[parent_index] not in excluded and row[child_index] not in excluded
        ],
        "comments": _rows_without_tasks(conn, "task_comments", "task_id", excluded),
        "runs": _rows_without_tasks(conn, "task_runs", "task_id", excluded),
        "events": _rows_without_tasks(conn, "task_events", "task_id", excluded),
        "attachments": _rows_without_tasks(
            conn, "task_attachments", "task_id", excluded
        ),
        "notification_checkpoints": _rows_without_tasks(
            conn, "kanban_notify_subs", "task_id", excluded
        ),
        "user_version": conn.execute("PRAGMA user_version").fetchone()[0],
    }


def _state_snapshot(conn, home, *target_ids):
    config_path = home / "config.yaml"
    return {
        "board": _non_target_snapshot(conn, *target_ids),
        "config": config_path.read_bytes(),
    }


def _seed_mixed_state_decoys(conn):
    decoys = {
        state: kb.create_task(conn, title=f"decoy-{state}", assignee="default")
        for state in (
            "todo",
            "ready",
            "review",
            "running",
            "blocked",
            "done",
            "archived",
            "stale",
            "failed",
        )
    }
    conn.execute("UPDATE tasks SET status='todo' WHERE id=?", (decoys["todo"],))
    conn.execute("UPDATE tasks SET status='review' WHERE id=?", (decoys["review"],))
    for state in ("blocked", "done", "archived"):
        conn.execute("UPDATE tasks SET status=? WHERE id=?", (state, decoys[state]))
    conn.commit()
    kb.link_tasks(conn, decoys["blocked"], decoys["todo"])

    running = kb.claim_task(conn, decoys["running"], claimer="fixture-running")
    assert running is not None
    kb._set_worker_pid(conn, decoys["running"], 7101)

    stale = kb.claim_task(conn, decoys["stale"], claimer="fixture-stale")
    assert stale is not None and stale.current_run_id is not None
    conn.execute(
        "UPDATE tasks SET claim_expires=1, last_heartbeat_at=1 WHERE id=?",
        (decoys["stale"],),
    )
    conn.execute(
        "UPDATE task_runs SET claim_expires=1, last_heartbeat_at=1 WHERE id=?",
        (stale.current_run_id,),
    )
    conn.commit()

    failed = kb.claim_task(conn, decoys["failed"], claimer="fixture-failed")
    assert failed is not None
    assert kb._record_spawn_failure(
        conn,
        decoys["failed"],
        "fixture failure",
        failure_limit=1,
    )

    for state, task_id in decoys.items():
        kb.add_notify_sub(
            conn,
            task_id=task_id,
            platform="fixture",
            chat_id=f"decoy-{state}",
            delivery_mode="notify",
        )
    conn.commit()

    expected_statuses = {
        "todo": "todo",
        "ready": "ready",
        "review": "review",
        "running": "running",
        "blocked": "blocked",
        "done": "done",
        "archived": "archived",
        "stale": "running",
        "failed": "blocked",
    }
    assert {
        state: kb.get_task(conn, task_id).status for state, task_id in decoys.items()
    } == expected_statuses
    stale_task = kb.get_task(conn, decoys["stale"])
    failed_task = kb.get_task(conn, decoys["failed"])
    assert stale_task.claim_expires == 1
    assert stale_task.last_heartbeat_at == 1
    assert failed_task.consecutive_failures == 1
    assert failed_task.last_failure_error == "fixture failure"
    assert {row[0] for row in conn.execute("SELECT task_id FROM task_runs")} == {
        decoys["running"],
        decoys["stale"],
        decoys["failed"],
    }
    assert {
        row[0] for row in conn.execute("SELECT DISTINCT task_id FROM task_events")
    } == set(decoys.values())
    checkpoints = dict(
        conn.execute("SELECT task_id, last_event_id FROM kanban_notify_subs")
    )
    assert set(checkpoints) == set(decoys.values())
    assert all(cursor > 0 for cursor in checkpoints.values())
    return decoys


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


def test_targeted_dispatch_claims_only_selected_and_retry_is_idempotent(mixed_board):
    conn, home = mixed_board
    target = kb.create_task(conn, title="target", assignee="default")
    before_decoys = _state_snapshot(conn, home, target)
    spawned = []

    first = adapter.dispatch_target(
        target,
        dry_run=False,
        config=_MIXED_STATE_CAPACITY,
        spawn_fn=lambda task, workspace, **kw: (
            spawned.append((task.id, workspace, kw)) or 4321
        ),
    )
    counts_after_first = _target_counts(conn, target)
    second = adapter.dispatch_target(
        target,
        dry_run=False,
        config=_MIXED_STATE_CAPACITY,
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
    assert _state_snapshot(conn, home, target) == before_decoys


def test_dry_run_reads_wal_and_has_no_logical_or_config_effects(mixed_board):
    conn, home = mixed_board
    target = kb.create_task(conn, title="target", assignee="default")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("UPDATE tasks SET priority = 7 WHERE id = ?", (target,))
    conn.commit()
    wal_path = Path(os.environ["HERMES_KANBAN_DB"] + "-wal")
    assert wal_path.stat().st_size > 0
    before = _state_snapshot(conn, home)

    result = adapter.dispatch_target(
        target,
        dry_run=True,
        config=_MIXED_STATE_CAPACITY,
        spawn_fn=lambda *a, **k: pytest.fail("dry-run spawned"),
    )

    assert result.dispatched is False
    assert result.error is None
    assert result.details["eligible"] is True
    assert _state_snapshot(conn, home) == before


@pytest.mark.parametrize("status", ["todo", "blocked", "done", "archived", "running"])
def test_ineligible_target_refuses_without_board_maintenance(mixed_board, status):
    conn, home = mixed_board
    target = kb.create_task(conn, title="target", assignee="default")
    conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, target))
    conn.commit()
    before = _state_snapshot(conn, home)

    result = adapter.dispatch_target(target, dry_run=False, config={})

    assert result.error == f"task {target} is not ready or review"
    assert _state_snapshot(conn, home) == before


@pytest.mark.parametrize("status", ["ready", "review"])
def test_atomic_claim_rejects_owner_change_without_run_or_event(mixed_board, status):
    conn, home = mixed_board
    target = kb.create_task(conn, title="target", assignee="default")
    conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, target))
    conn.commit()
    observed = "default"
    conn.execute("UPDATE tasks SET assignee = 'other' WHERE id = ?", (target,))
    conn.commit()
    before = _target_counts(conn, target)
    before_decoys = _state_snapshot(conn, home, target)

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
    assert _state_snapshot(conn, home, target) == before_decoys


def test_real_cross_process_lock_contention_fails_closed(mixed_board):
    conn, home = mixed_board
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
        before = _state_snapshot(conn, home)
        result = adapter.dispatch_target(target, dry_run=True, config={})
        assert result.error == "dispatch lock is held"
        assert _state_snapshot(conn, home) == before
    finally:
        release.put(True)
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert process.exitcode == 0


def test_spawn_failure_uses_native_retry_cleanup_and_failure_cap(mixed_board):
    conn, home = mixed_board
    target = kb.create_task(conn, title="target", assignee="default")
    before_decoys = _state_snapshot(conn, home, target)

    result = adapter.dispatch_target(
        target,
        dry_run=False,
        config={**_MIXED_STATE_CAPACITY, "failure_limit": 1},
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
    assert _state_snapshot(conn, home, target) == before_decoys


def test_native_respawn_guard_refuses_before_claim(mixed_board):
    conn, home = mixed_board
    target = kb.create_task(conn, title="guarded", assignee="default")
    conn.execute(
        "UPDATE tasks SET last_failure_error='401 unauthorized credential' WHERE id=?",
        (target,),
    )
    conn.commit()
    before = _target_counts(conn, target)
    before_state = _state_snapshot(conn, home)

    result = adapter.dispatch_target(
        target, dry_run=False, config=_MIXED_STATE_CAPACITY
    )

    assert result.error == "respawn guarded: blocker_auth"
    assert result.details == {"guard": "blocker_auth"}
    assert kb.get_task(conn, target).status == "ready"
    assert _target_counts(conn, target) == before
    assert _state_snapshot(conn, home) == before_state


def test_review_dispatch_preserves_review_lane_and_skill(mixed_board):
    conn, home = mixed_board
    target = kb.create_task(conn, title="review", assignee="default")
    conn.execute("UPDATE tasks SET status = 'review' WHERE id = ?", (target,))
    conn.commit()
    seen = []
    before_decoys = _state_snapshot(conn, home, target)

    result = adapter.dispatch_target(
        target,
        dry_run=False,
        config={**_MIXED_STATE_CAPACITY, "review_dispatch": True},
        spawn_fn=lambda task, workspace, **kw: seen.append(task) or 91,
    )

    assert result.dispatched is True
    assert "sdlc-review" in seen[0].skills
    event = conn.execute(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='claimed' ORDER BY id DESC",
        (target,),
    ).fetchone()[0]
    assert '"source_status": "review"' in event
    assert _state_snapshot(conn, home, target) == before_decoys


def test_missing_and_tombstoned_profiles_refuse(mixed_board):
    conn, home = mixed_board
    missing = kb.create_task(conn, title="missing", assignee="missing")
    profile = home / "profiles" / "worker"
    profile.mkdir(parents=True)
    tombstone = profile.parent / ".deleted" / "worker"
    tombstone.parent.mkdir()
    tombstone.write_text("deleted\n", encoding="utf-8")
    deleted = kb.create_task(conn, title="deleted", assignee="worker")
    before = _state_snapshot(conn, home)

    missing_result = adapter.dispatch_target(missing, dry_run=True, config={})
    deleted_result = adapter.dispatch_target(deleted, dry_run=True, config={})

    assert missing_result.error == "assignee 'missing' is not a live Hermes profile"
    assert deleted_result.error == "assignee 'worker' is not a live Hermes profile"
    assert _state_snapshot(conn, home) == before


def test_capacity_and_pressure_guards_refuse_before_claim(mixed_board, monkeypatch):
    conn, home = mixed_board
    target = kb.create_task(conn, title="target", assignee="default")
    before = _state_snapshot(conn, home)

    board_cap = adapter.dispatch_target(
        target,
        dry_run=True,
        config={**_MIXED_STATE_CAPACITY, "max_spawn": 2},
    )
    profile_cap = adapter.dispatch_target(
        target,
        dry_run=True,
        config={**_MIXED_STATE_CAPACITY, "max_in_progress_per_profile": 2},
    )
    monkeypatch.setattr(kb, "_memory_pressure_level", lambda: "critical")
    pressure = adapter.dispatch_target(
        target, dry_run=True, config=_MIXED_STATE_CAPACITY
    )

    assert board_cap.error == "board concurrency cap reached"
    assert profile_cap.error == "per-profile concurrency cap reached"
    assert pressure.error == "system memory pressure is critical"
    assert _state_snapshot(conn, home) == before


def test_missing_board_refuses_without_creating_paths(tmp_path, monkeypatch):
    missing = tmp_path / "missing" / "kanban.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(missing))

    result = adapter.dispatch_target("t_missing", dry_run=False, config={})

    assert result.error == f"database is not initialized: {missing}"
    assert not missing.parent.exists()


def test_unknown_and_stale_selected_tasks_refuse_without_maintenance(mixed_board):
    conn, home = mixed_board
    stale = kb.create_task(conn, title="stale", assignee="default")
    conn.execute(
        "UPDATE tasks SET status='running', claim_lock='old', claim_expires=1 "
        "WHERE id=?",
        (stale,),
    )
    conn.commit()
    before = _state_snapshot(conn, home)

    unknown = adapter.dispatch_target("t_unknown", dry_run=False, config={})
    stale_result = adapter.dispatch_target(stale, dry_run=False, config={})

    assert unknown.error == "task t_unknown not found"
    assert stale_result.error == f"task {stale} is not ready or review"
    assert _state_snapshot(conn, home) == before


def test_parent_review_gate_and_review_default_assignment_refuse_read_only(mixed_board):
    conn, home = mixed_board
    parent = kb.create_task(conn, title="parent", assignee="default")
    child = kb.create_task(conn, title="child", assignee="default", parents=[parent])
    review = kb.create_task(conn, title="review", assignee="default")
    unowned_review = kb.create_task(conn, title="unowned-review", assignee=None)
    conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (child,))
    conn.execute(
        "UPDATE tasks SET status='review' WHERE id IN (?, ?)", (review, unowned_review)
    )
    conn.commit()
    before = _state_snapshot(conn, home)

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
    assert _state_snapshot(conn, home) == before


def test_default_assignment_previews_without_write_then_persists_with_claim(
    mixed_board,
):
    conn, home = mixed_board
    target = kb.create_task(conn, title="target", assignee=None)
    before_preview = _state_snapshot(conn, home)
    before_decoys = _state_snapshot(conn, home, target)

    preview = adapter.dispatch_target(
        target,
        dry_run=True,
        config={**_MIXED_STATE_CAPACITY, "default_assignee": "default"},
        spawn_fn=lambda *a, **k: pytest.fail("preview spawned"),
    )

    assert preview.error is None
    assert preview.assignee == "default"
    assert preview.details["used_default_assignee"] is True
    assert kb.get_task(conn, target).assignee is None
    assert _state_snapshot(conn, home) == before_preview

    launched = adapter.dispatch_target(
        target,
        dry_run=False,
        config={**_MIXED_STATE_CAPACITY, "default_assignee": "default"},
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
    assert _state_snapshot(conn, home, target) == before_decoys


def test_lock_sidecar_open_failure_refuses_without_board_write(
    mixed_board, monkeypatch
):
    conn, home = mixed_board
    target = kb.create_task(conn, title="target", assignee="default")
    conn.commit()
    before = _state_snapshot(conn, home)
    original_open = Path.open

    def fail_lock(path, *args, **kwargs):
        if path.name.endswith(".dispatch.lock"):
            raise PermissionError("denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_lock)
    result = adapter.dispatch_target(target, dry_run=False, config={})

    assert result.error == "dispatch lock unavailable"
    assert _state_snapshot(conn, home) == before


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


def test_real_other_board_consumes_host_capacity(mixed_board, monkeypatch):
    conn, home = mixed_board
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    kb.create_board("second")
    with kb.connect(board="second") as other:
        running = kb.create_task(other, title="busy", assignee="default")
        assert kb.claim_task(other, running) is not None
    target = kb.create_task(conn, title="target", assignee="default")
    before = _state_snapshot(conn, home)

    result = adapter.dispatch_target(
        target,
        dry_run=True,
        config={"max_in_progress": 3},
        board="default",
    )

    assert result.error == "host concurrency cap reached"
    assert _state_snapshot(conn, home) == before


def test_missing_registered_other_board_fails_closed(mixed_board, monkeypatch):
    conn, home = mixed_board
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    kb.create_board("vanished")
    missing_db = kb.kanban_db_path(board="vanished")
    missing_db.unlink()
    target = kb.create_task(conn, title="target", assignee="default")
    before = _state_snapshot(conn, home)

    result = adapter.dispatch_target(
        target,
        dry_run=True,
        config={"max_in_progress": 64},
        board="default",
    )

    assert result.error == "cannot verify host concurrency cap"
    assert not missing_db.exists()
    assert _state_snapshot(conn, home) == before
