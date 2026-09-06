"""Release-pinned exact-task lifecycle adapter.

This module intentionally depends on private Hermes internals. It is imported
only after :mod:`compat` admits the exact source hashes.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import os
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Mapping

import yaml

# Private release dependency. Production reaches this import only after
# compat.check_compatibility() admits the exact source hashes. The lifecycle
# below is adapted from accepted patch be9d18ca5d3c27ad077642938ffa38aa09a5ed01.
from hermes_cli import kanban_db as kb


@dataclass
class DispatchOutcome:
    task_id: str
    dry_run: bool
    dispatched: bool = False
    assignee: str | None = None
    workspace: str | None = None
    error: str | None = None
    auto_blocked: bool = False
    details: dict[str, object] = field(default_factory=dict)


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return result if result is not None and result >= 1 else None


@contextlib.contextmanager
def strict_dispatch_lock(db_path: Path):
    """Acquire the native ``<kanban.db>.dispatch.lock`` identity or fail closed."""
    lock_path = db_path.with_name(db_path.name + ".dispatch.lock")
    handle = None
    acquired: bool | None = False
    try:
        handle = lock_path.open("a+b")
        if os.name == "nt":
            try:
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except (OSError, AttributeError):
                acquired = False
        elif os.name == "posix":
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (BlockingIOError, OSError):
                acquired = False
        else:
            acquired = None
    except OSError:
        acquired = None
    try:
        yield acquired
    finally:
        if handle is not None:
            try:
                if acquired:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    elif os.name == "posix":
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except (OSError, AttributeError):
                pass
            finally:
                handle.close()


def _open_board(db_path: Path, *, readonly: bool) -> sqlite3.Connection:
    mode = "ro" if readonly else "rw"
    conn = sqlite3.connect(
        f"{db_path.resolve().as_uri()}?mode={mode}", uri=True, timeout=120
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")
    if not readonly:
        conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _validate_schema(conn: sqlite3.Connection) -> str | None:
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    required_tables = {
        "tasks",
        "task_links",
        "task_comments",
        "task_runs",
        "task_events",
    }
    if not required_tables.issubset(tables):
        return "database schema is not initialized/current"
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    if not set(kb.Task.__dataclass_fields__).issubset(columns):
        return "database schema is not initialized/current"
    return None


def _profile_exists(name: str) -> bool:
    canonical = name.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", canonical):
        return False
    if canonical == "default":
        return True
    from hermes_constants import get_default_hermes_root, named_profile_is_deleted

    profile = get_default_hermes_root() / "profiles" / canonical
    return profile.is_dir() and not named_profile_is_deleted(profile)


def _count_other_boards_readonly(board: str | None) -> int:
    current = kb.kanban_db_path(board=board).expanduser().resolve()
    try:
        boards = kb.list_boards(include_archived=False)
    except Exception as exc:
        raise RuntimeError("cannot enumerate boards for host cap") from exc
    total = 0
    for metadata in boards:
        slug = metadata.get("slug") or kb.DEFAULT_BOARD
        path = kb.kanban_db_path(board=slug).expanduser().resolve()
        if path == current:
            continue
        if not path.is_file():
            raise RuntimeError(f"cannot read board {slug!r} for host cap")
        try:
            with contextlib.closing(_open_board(path, readonly=True)) as other:
                if _validate_schema(other) is not None:
                    raise RuntimeError("schema is not current")
                total += int(
                    other.execute(
                        "SELECT COUNT(*) FROM tasks WHERE status='running'"
                    ).fetchone()[0]
                )
        except Exception as exc:
            raise RuntimeError(f"cannot read board {slug!r} for host cap") from exc
    return total


def _assess_target(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    config: Mapping[str, object],
    board: str | None,
) -> tuple[sqlite3.Row | None, str | None, str | None, dict[str, object]]:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return None, None, f"task {task_id} not found", {}
    source_status = row["status"]
    if source_status not in {"ready", "review"}:
        return row, None, f"task {task_id} is not ready or review", {}
    if row["claim_lock"] is not None:
        return row, None, f"task {task_id} already has a live claim", {}
    if source_status == "review" and config.get("review_dispatch", True) is not True:
        return row, None, "review dispatch is disabled", {}
    if not kb._parents_satisfied(conn, task_id):
        return row, None, "unsatisfied parent dependencies", {}

    assignee = (row["assignee"] or "").strip()
    fallback = str(config.get("default_assignee") or "").strip()
    used_default = False
    if not assignee and fallback and source_status == "ready":
        assignee = fallback
        used_default = True
    if not assignee:
        return row, None, "task has no assignee", {}
    if not _profile_exists(assignee):
        return row, assignee, f"assignee {assignee!r} is not a live Hermes profile", {}

    pressure = kb._memory_pressure_level()
    if pressure == "critical":
        return (
            row,
            assignee,
            "system memory pressure is critical",
            {"memory_pressure": pressure},
        )
    running = int(
        conn.execute("SELECT COUNT(*) FROM tasks WHERE status='running'").fetchone()[0]
    )
    max_spawn = _positive_int(config.get("max_spawn"))
    if max_spawn is not None and running >= max_spawn:
        return row, assignee, "board concurrency cap reached", {"running": running}
    configured_host_cap = _positive_int(config.get("max_in_progress"))
    host_cap = kb.resolve_max_in_progress(configured_host_cap)
    if host_cap is not None:
        try:
            host_running = running + _count_other_boards_readonly(board)
        except Exception:
            return row, assignee, "cannot verify host concurrency cap", {}
        if host_running >= host_cap:
            return (
                row,
                assignee,
                "host concurrency cap reached",
                {"running": host_running},
            )
    profile_cap = _positive_int(config.get("max_in_progress_per_profile"))
    if profile_cap is not None:
        profile_running = int(
            conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status='running' AND assignee=?",
                (assignee,),
            ).fetchone()[0]
        )
        if profile_running >= profile_cap:
            return (
                row,
                assignee,
                "per-profile concurrency cap reached",
                {"running": profile_running},
            )
    guard = kb.check_respawn_guard(
        conn, task_id, lane="review" if source_status == "review" else "ready"
    )
    if guard is not None:
        return row, assignee, f"respawn guarded: {guard}", {"guard": guard}
    return (
        row,
        assignee,
        None,
        {
            "eligible": True,
            "source_status": source_status,
            "used_default_assignee": used_default,
            "memory_pressure": pressure,
        },
    )


def _claim_expected_in_txn(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    source_status: str,
    expected_assignee: str,
    ttl_seconds: int | None,
):
    now = int(time.time())
    lock = kb._claimer_id()
    expires = now + kb._resolve_claim_ttl_seconds(ttl_seconds)
    if source_status == "ready":
        stale = conn.execute(
            "SELECT current_run_id FROM tasks WHERE id=? AND status='ready' AND assignee=?",
            (task_id, expected_assignee),
        ).fetchone()
        if stale and stale["current_run_id"]:
            conn.execute(
                """
                UPDATE task_runs
                   SET status='reclaimed', outcome='reclaimed',
                       summary=COALESCE(summary, 'invariant recovery on re-claim'),
                       ended_at=?, claim_lock=NULL, claim_expires=NULL, worker_pid=NULL
                 WHERE id=? AND ended_at IS NULL
                """,
                (now, int(stale["current_run_id"])),
            )
    updated = conn.execute(
        """
        UPDATE tasks
           SET status='running', claim_lock=?, claim_expires=?,
               started_at=COALESCE(started_at, ?)
         WHERE id=? AND status=? AND claim_lock IS NULL AND assignee=?
        """,
        (lock, expires, now, task_id, source_status, expected_assignee),
    )
    if updated.rowcount != 1:
        return None
    trow = conn.execute(
        "SELECT assignee, max_runtime_seconds, current_step_key FROM tasks WHERE id=?",
        (task_id,),
    ).fetchone()
    run = conn.execute(
        """
        INSERT INTO task_runs (
            task_id, profile, step_key, status, claim_lock, claim_expires,
            max_runtime_seconds, started_at
        ) VALUES (?, ?, ?, 'running', ?, ?, ?, ?)
        """,
        (
            task_id,
            trow["assignee"],
            trow["current_step_key"],
            lock,
            expires,
            trow["max_runtime_seconds"],
            now,
        ),
    )
    run_id = int(run.lastrowid)
    conn.execute("UPDATE tasks SET current_run_id=? WHERE id=?", (run_id, task_id))
    payload: dict[str, object] = {"lock": lock, "expires": expires, "run_id": run_id}
    if source_status == "review":
        payload["source_status"] = "review"
    kb._append_event(conn, task_id, "claimed", payload, run_id=run_id)
    return kb.get_task(conn, task_id)


def _claim_expected(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    source_status: str,
    expected_assignee: str,
    ttl_seconds: int | None,
):
    """Atomic expected-owner claim, exposed narrowly for race regression tests."""
    with kb.write_txn(conn):
        claimed = _claim_expected_in_txn(
            conn,
            task_id=task_id,
            source_status=source_status,
            expected_assignee=expected_assignee,
            ttl_seconds=ttl_seconds,
        )
    if claimed is not None and source_status == "ready":
        kb._fire_kanban_lifecycle_hook(
            "kanban_task_claimed",
            task_id,
            board=kb.get_current_board(),
            assignee=claimed.assignee,
            run_id=claimed.current_run_id,
        )
    return claimed


def dispatch_target(
    task_id: str,
    *,
    dry_run: bool,
    config: Mapping[str, object],
    spawn_fn: Callable | None = None,
    board: str | None = None,
    ttl_seconds: int | None = None,
) -> DispatchOutcome:
    """Preview or dispatch one exact task without queue-wide maintenance."""
    outcome = DispatchOutcome(task_id=task_id, dry_run=dry_run)
    if not isinstance(task_id, str) or not re.fullmatch(r"[^\s]+", task_id):
        outcome.error = "task id must be a non-empty token"
        return outcome
    try:
        db_path = kb.kanban_db_path(board=board).expanduser()
    except Exception:
        outcome.error = "dispatch lock path unavailable"
        return outcome
    if not db_path.is_file():
        outcome.error = f"database is not initialized: {db_path}"
        return outcome
    try:
        with contextlib.closing(_open_board(db_path, readonly=True)) as probe:
            schema_error = _validate_schema(probe)
    except Exception as exc:
        outcome.error = f"database unavailable: {exc}"
        return outcome
    if schema_error is not None:
        outcome.error = schema_error
        return outcome

    with strict_dispatch_lock(db_path) as held:
        if held is None:
            outcome.error = "dispatch lock unavailable"
            return outcome
        if not held:
            outcome.error = "dispatch lock is held"
            return outcome
        try:
            conn = _open_board(db_path, readonly=dry_run)
        except Exception as exc:
            outcome.error = f"database unavailable: {exc}"
            return outcome
        with contextlib.closing(conn):
            schema_error = _validate_schema(conn)
            if schema_error is not None:
                outcome.error = schema_error
                return outcome
            if dry_run:
                row, assignee, error, details = _assess_target(
                    conn, task_id=task_id, config=config, board=board
                )
                outcome.assignee = assignee
                outcome.error = error
                outcome.details = details
                return outcome

            with kb.write_txn(conn):
                row, assignee, error, details = _assess_target(
                    conn, task_id=task_id, config=config, board=board
                )
                outcome.assignee = assignee
                outcome.details = details
                if error is not None:
                    outcome.error = error
                    return outcome
                assert row is not None and assignee is not None
                if details.get("used_default_assignee"):
                    assigned = conn.execute(
                        """
                        UPDATE tasks SET assignee=?
                         WHERE id=? AND status='ready' AND claim_lock IS NULL
                           AND (assignee IS NULL OR assignee='')
                        """,
                        (assignee, task_id),
                    )
                    if assigned.rowcount != 1:
                        outcome.error = "task changed during default assignment"
                        return outcome
                    kb._append_event(
                        conn,
                        task_id,
                        "assigned",
                        {"assignee": assignee, "source": "kanban.default_assignee"},
                    )
                source_status = str(row["status"])
                claimed = _claim_expected_in_txn(
                    conn,
                    task_id=task_id,
                    source_status=source_status,
                    expected_assignee=assignee,
                    ttl_seconds=ttl_seconds,
                )
                if claimed is None:
                    outcome.error = "task became ineligible before claim"
                    return outcome

            if source_status == "ready":
                kb._fire_kanban_lifecycle_hook(
                    "kanban_task_claimed",
                    task_id,
                    board=board or kb.get_current_board(),
                    assignee=claimed.assignee,
                    run_id=claimed.current_run_id,
                )
            try:
                resolved_branch = None
                if claimed.workspace_kind == "worktree":
                    workspace, resolved_branch = kb._resolve_worktree_workspace(
                        claimed, board=board
                    )
                else:
                    workspace = kb.resolve_workspace(claimed, board=board)
                kb.set_workspace_path(conn, claimed.id, str(workspace))
                if claimed.workspace_kind == "worktree":
                    kb.set_branch_name(
                        conn,
                        claimed.id,
                        resolved_branch
                        or (claimed.branch_name or "").strip()
                        or f"wt/{claimed.id}",
                    )
                kb._maybe_emit_scratch_tip(conn, claimed.id, claimed.workspace_kind)
                if source_status == "review":
                    claimed.skills = list(
                        dict.fromkeys([*(claimed.skills or []), "sdlc-review"])
                    )
                spawn = spawn_fn if spawn_fn is not None else kb._default_spawn
                try:
                    signature = inspect.signature(spawn)
                    pid = (
                        spawn(claimed, str(workspace), board=board)
                        if "board" in signature.parameters
                        else spawn(claimed, str(workspace))
                    )
                except (TypeError, ValueError):
                    pid = spawn(claimed, str(workspace))
                if pid:
                    kb._set_worker_pid(conn, claimed.id, int(pid))
                kb._fire_worker_spawned_hook(
                    conn, claimed, str(workspace), pid, board=board
                )
                outcome.dispatched = True
                outcome.workspace = str(workspace)
            except Exception as exc:
                failure_limit = _positive_int(config.get("failure_limit"))
                blocked = kb._record_spawn_failure(
                    conn,
                    claimed.id,
                    str(exc),
                    failure_limit=failure_limit,
                )
                outcome.auto_blocked = blocked
                outcome.error = f"spawn failed: {exc}"
            return outcome


def run_command(args, *, admission) -> int:
    home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
    config_path = home / "config.yaml"
    try:
        if config_path.is_file():
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        else:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError("config root must be a mapping")
        config = raw.get("kanban", {})
        if not isinstance(config, dict):
            raise ValueError("kanban config must be a mapping")
    except Exception as exc:
        outcome = DispatchOutcome(
            task_id=args.task_id,
            dry_run=bool(args.dry_run),
            error=f"cannot read dispatch configuration: {exc}",
        )
    else:
        outcome = dispatch_target(
            args.task_id,
            dry_run=bool(args.dry_run),
            config=config,
        )
    payload = asdict(outcome)
    payload["ok"] = outcome.error is None
    payload["release_sources"] = {
        name: str(path) for name, path in admission.origins.items()
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, sort_keys=True))
    elif outcome.error is not None:
        print(f"A1 task dispatch refused: {outcome.error}")
    elif outcome.dry_run:
        print(f"A1 task dispatch preview: {outcome.task_id} is eligible")
    else:
        print(f"A1 task dispatched: {outcome.task_id} -> {outcome.assignee}")
    return 0 if outcome.error is None else 2
