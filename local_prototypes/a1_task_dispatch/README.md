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
verifies any already-loaded module has the same origin, requires every pin to
come from one coherent checkout, and checks these release-29112 SHA-256 pins.
The manifest contains 136 source files: the 129 release sources observed while
the 16 fixed adapter/helper seeds initialize, six more first-party sources
loaded by the ready-claim lifecycle hook, and the conservatively retained
`gateway.platform_registry` pin. The observed set
includes 39 release-shipped bundled provider initializers and the helper sources
they import. `compat.py` is the finite exact pin and path inventory.

The authoritative test/development completeness oracle imports the fixed seeds
in a fresh subprocess under a disposable, credential-free `HOME` and
`HERMES_HOME`. An audit hook refuses network and child-process effects, and the
oracle collects loaded local `.py` origins independently of both the production
manifest and the AST walker. A second isolated trace invokes the adapter's
`kanban_db._fire_kanban_lifecycle_hook("kanban_task_claimed", ...)` entrypoint
with fixture event data, exercising its real lazy first-party initialization.
It uses a test-only Python profiling callback to stop at entry to
`hermes_cli.plugins.invoke_hook`, before general plugin discovery or enabled
consumer delivery. No board claim or worker launch is needed for this focused
hook inventory, and no release function is replaced. The resulting 135-source
inventory must be covered by the manifest, with matching source paths.
A separate static utility remains as a conservative
supplement: it walks visible module imports, package parents, executed class
bodies, and same-module named helpers. That static walk is intentionally not
described as complete because it cannot follow initialization calls into
functions imported from other modules.

The release-bundled provider initializers reached by config initialization are
pinned even though the provider registry discovers them dynamically. The fixed
first-party claim-hook chain is also pinned: `hermes_cli.observability`, its
`relay_shared_metrics`, `shared_metrics`, `shared_metrics_contract`, and
`shared_metrics_subscriber` modules, and `agent.relay_runtime`. These execute
through `hermes_cli.lifecycle.invoke_hook` after the durable claim and before
spawn, so they are part of source admission. External enabled hook consumers
remain a separate runtime boundary; user and pip extension implementations are
excluded by the disposable home, opt-in configuration, and plugin delivery
boundary described above.

Admission also checks the exact effective membership of the two fixed release
directories that provider discovery scans: bundled
`plugins/model-providers/<name>/__init__.py` sources and legacy `providers`
modules other than private names and `base`. An added discoverable release
member refuses even when every previously pinned file is unchanged. This check
does not enumerate user-home providers or pip entry points.

The manifest follows the actual helper path: Kanban import-time support;
delegated-child and SQLite file-integrity checks around the claim transaction;
memory sampling and pressure classification used for admission; lifecycle and
profile helpers; and the session, config, toolset, and state helpers used while
preparing the native spawn. The claim-TTL helper is contained in pinned
`kanban_db.py` and reads only its explicit argument or the process environment;
it does not import the Hermes config module.

An unavailable file, origin mismatch, or changed hash refuses the command before
the plugin imports its private dispatcher and before any plugin dispatch lock or
board access. Any Hermes release change requires a new source review and new
pins; there is no compatibility fallback.

The host/handler boundary matters. `hermes_cli.main`, the plugin loader, config
startup, enabled plugins/providers, and package initializers may already have
executed before this plugin handler can check them. Admission therefore cannot
retroactively prevent host-startup effects; package initializer pins are
compatibility checks on source the adapter depends on, not a claim that their
code has not run. Admission rejects a mismatch before plugin-initiated private
imports and dispatch effects. It is not a sandbox and does not protect against
arbitrary malicious code already running in the process.

The source boundary ends at the native `subprocess.Popen` worker-launch call.
Python standard-library behavior, installed external dependencies, OS-provided
measurements and lock/filesystem semantics, and the public PyYAML package are
not release-shipped Hermes source modules and are not hash-pinned. Host startup
before the fixed seed imports, third-party extension consumers, and the launched
child's Hermes/provider/model/credential/network behavior are separate runtime
boundaries. The release-fixed pre-launch helper sources reached by seed
initialization and the first-party claim hook are pinned, but this prototype
still does not prove a real worker
runtime.

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
second enabled inert plugin; required/invalid IDs; changed direct,
cross-module initializer-helper, first-party claim-hook, and bundled-provider
compatibility pins;
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
