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
actually load without importing those modules or their package parents,
verifies any already-loaded module has the same origin, requires the complete
manifest to come from one coherent checkout, and checks these release-29112
SHA-256 pins:

| Module | SHA-256 |
| --- | --- |
| `hermes_cli.kanban` | `8e4e51a1403171e706aea60b703eb141e7909615d845efdbc101dfd8cdc6bca0` |
| `hermes_cli.kanban_db` | `4a4495bb23e5f5c417d4259d759db5acbfc54d9043f1cd46b56724dbcc8a5fe0` |
| `hermes_constants` | `b78449440ce5149c3896129f9ac309e2ebe70a265b5cae08b4316b7a6910f068` |
| `hermes_cli.sqlite_util` | `55f41975ed3af6b15c3ca2e2a9dc782b7b99477dd36bfe48d11f462743e56438` |
| `toolsets` | `31752adbf72647367b1ba31848f8be06001df706d12d1fdcd2fabd38f284de85` |
| `tools.registry` | `f95d011cae75f0426dd77cb1e25df6bebb61a067d795312df297cb877dd616f5` |
| `agent.delegation_context` | `d185d1746efa5516a2697dc7e58b1e049300239300475766e91decbfc0c2f754` |
| `hermes_cli.sqlite_safe_read` | `119a8435496d6136cdb7267a4180158308590ee129a16a1cedd39932323d03cb` |
| `gateway.lifecycle_ledger` | `85b7952df35aecf5cc46093019d3e4042e18de28c3fb00eb982ad1b69e49d1c0` |
| `gateway.memory_status` | `d63fac1fe71761808f95cd1373dacac288a571bc57593a08ef33de783b007f02` |
| `hermes_cli.lifecycle` | `e820ed8114d7062d53223113cc053c737d4ced6b624ce1fbe88f79ad8b2a26bd` |
| `hermes_cli.profiles` | `edeafa558cea28cc42ce4e80a21489a3176454a48bae9f767d8c179b954e0981` |
| `gateway.session_context` | `b68908ffec7891b12080933a4d50a63d449479a596335c2579776c6e545c4500` |
| `hermes_cli.config` | `cbce38a2aeed3838dc768d69ee0b31278a211f64bfec66bf786f571c775b3730` |
| `hermes_cli.tools_config` | `f6edc900cbb16ada9313d004820ae054be84428b1c3bab729555c50a4499bb65` |
| `hermes_state` | `696fd2d37d66a599228f5b1be5d5b76423b744149c83a2fbd59bf3f45a5f3891` |

The manifest follows the actual helper path: Kanban import-time support;
delegated-child and SQLite file-integrity checks around the claim transaction;
memory sampling and pressure classification used for admission; lifecycle and
profile helpers; and the session, config, toolset, and state helpers used while
preparing the native spawn. The claim-TTL helper is contained in pinned
`kanban_db.py` and reads only its explicit argument or the process environment;
it does not import the Hermes config module.

An unavailable file, origin mismatch, or changed hash refuses the command before
the private dispatcher import and before any board access. Any Hermes release
change requires a new source review and new pins; there is no compatibility
fallback.

The source boundary ends at the native `subprocess.Popen` call. Python
standard-library behavior, OS-provided measurements and lock/filesystem
semantics, and the public PyYAML package are not Hermes source modules and are
not hash-pinned. Dynamically discovered hook consumers and the spawned child's
Hermes/provider/model/network behavior are separate runtime boundaries; the
native hook dispatcher and all pre-launch Hermes helpers named above are
pinned, but this prototype still does not prove a real worker runtime.

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
