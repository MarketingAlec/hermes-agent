# A1 exact-task dispatch local prototype

This is a disposable, source-only user-plugin prototype for one narrow command:

```text
hermes a1-task-dispatch --task-id ID [--dry-run] [--json]
```

It is **not** a bundled Hermes plugin, a general queue dispatcher, or approval
to install or run anything on Mini, Cloud, or a real Hermes home. The prototype
exists under `local_prototypes/` so its lifecycle can be assessed without core
edits. It registers no model tool, hook, schedule, queue, or background task.

## Compatibility admission

The adapter deliberately uses unsupported private Hermes APIs. Before importing
them, the command resolves the source files the current Python process would
actually load, verifies any already-loaded module has the same origin, requires
all three files to come from one coherent checkout, and checks these SHA-256
pins:

| Module | SHA-256 |
| --- | --- |
| `hermes_cli.kanban` | `8e4e51a1403171e706aea60b703eb141e7909615d845efdbc101dfd8cdc6bca0` |
| `hermes_cli.kanban_db` | `4a4495bb23e5f5c417d4259d759db5acbfc54d9043f1cd46b56724dbcc8a5fe0` |
| `hermes_constants` | `b78449440ce5149c3896129f9ac309e2ebe70a265b5cae08b4316b7a6910f068` |

An unavailable file, origin mismatch, or changed hash refuses the command before
the private dispatcher import and before any board access. Any Hermes release
change requires a new source review and new pins; there is no compatibility
fallback.

## Lifecycle contract

The command requires an explicit, non-whitespace task ID. It inspects only that
task and never falls back to queue-wide dispatch or maintenance. Eligible tasks
must already be `ready` or `review`, have satisfied parents, have no live claim,
and resolve to a non-deleted Hermes profile. Review dispatch obeys the existing
config gate and never receives the ready-lane default assignee.

The adapter preserves the native database, card, run, event, workspace, branch,
worker hook, retry cleanup, respawn guard, memory-pressure, board-cap, host-cap,
per-profile-cap, and failure-cap behavior. It acquires the same
`<kanban.db>.dispatch.lock` identity with a real non-blocking OS file lock and
fails closed if the lock or complete cross-board evidence is unavailable. Both
ready and review claims atomically include the observed assignee, so an owner
change cannot create a run or event for the stale owner.

`--dry-run` opens the existing database with SQLite `mode=ro`, sees committed
WAL content, and does not initialize, migrate, checkpoint, or logically mutate
the database. Missing and outdated databases refuse. The command reads
`kanban` settings from the existing `config.yaml`; malformed configuration
refuses and is never repaired or rewritten.

## Test-only use

The portable smoke runner is the supported way to exercise this prototype:

```text
python3.13 -m local_prototypes.a1_task_dispatch.testing.run_portable
```

Run it from the exact release-derived source checkout with the project's pinned
dependencies installed. It creates a temporary `HOME` and `HERMES_HOME`, copies
the plugin only into that temporary user-plugin directory, strips credential
variables, creates a native temporary SQLite board, and invokes the full public
`python -m hermes_cli.main ... --dry-run --json` path. It refuses non-3.13 and
non-macOS/Linux interpreters.

The public CLI tests also cover disabled, missing, and malformed plugins; a
second enabled inert plugin; required/invalid IDs; changed compatibility pins;
default and named-profile isolation; and malformed config. Whole-tree and
logical-database snapshots protect `config.yaml`, `SOUL.md`, permissions, all
board tables, and pre-existing plugin files. The observed upstream startup
effects in the disposable home are disclosed rather than called write-free:

- logging creates or appends `logs/agent.log` and `logs/errors.log`;
- SQLite WAL reading can materialize `kanban.db-wal` and `kanban.db-shm`;
- strict locking creates the empty `kanban.db.dispatch.lock` sidecar.

Unit lifecycle tests use real temporary SQLite databases, a real committed WAL,
and a real separate process for lock contention. The only worker boundary
interception is an injected low-level spawn callable that returns an inert PID
or raises a controlled error; no Hermes worker, provider, credential, network,
or Cloud tool authority is exercised. A green run therefore proves source
lifecycle behavior, not real Cloud readiness, remote plugin enablement, or
runtime admission.

## Rollback

Rollback is removal of the disposable source fixtures only:

- `local_prototypes/a1_task_dispatch/`
- `tests/local_prototypes/test_a1_task_dispatch_*.py`

The harness uses temporary directories that are deleted on exit. It makes no
real-home installation to undo. Do not translate this rollback note into an
instruction to remove any real plugin or runtime state.
