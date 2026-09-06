"""Hermetic public-CLI fixture for the local plugin prototype.

The fixture creates a throwaway HOME/HERMES_HOME, copies the plugin only into
that home, initializes a native temporary Kanban database, and invokes the
real ``python -m hermes_cli.main`` entry point.  It never points at a live
Hermes home and never launches a worker because every public invocation made
by this harness uses ``--dry-run``.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_SOURCE = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Fixture:
    root: Path
    home: Path
    hermes_root: Path
    hermes_home: Path
    db_path: Path
    profile: str


def make_fixture(root: Path, *, profile: str = "default") -> Fixture:
    home = root / "home"
    hermes_root = home / ".hermes"
    hermes_home = (
        hermes_root if profile == "default" else hermes_root / "profiles" / profile
    )
    hermes_home.mkdir(parents=True)
    (root / "tmp").mkdir()
    return Fixture(
        root=root,
        home=home,
        hermes_root=hermes_root,
        hermes_home=hermes_home,
        db_path=hermes_home / "kanban.db",
        profile=profile,
    )


def isolated_env(fixture: Fixture, *, repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """Return a minimal credential-free environment for a fixture process."""
    allowed = {
        "PATH",
        "LANG",
        "LC_ALL",
        "TZ",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update({
        "HOME": str(fixture.home),
        "HERMES_HOME": str(fixture.hermes_root),
        "HERMES_KANBAN_DB": str(fixture.db_path),
        "PYTHONPATH": str(repo_root),
        "TMPDIR": str(fixture.root / "tmp"),
        "NO_COLOR": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
    })
    return env


def install_plugin(fixture: Fixture, *, changed_pin: bool = False) -> Path:
    destination = fixture.hermes_home / "plugins" / "a1_task_dispatch"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        PLUGIN_SOURCE,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "testing"),
    )
    if changed_pin:
        compat = destination / "compat.py"
        source = compat.read_text(encoding="utf-8")
        known = "4a4495bb23e5f5c417d4259d759db5acbfc54d9043f1cd46b56724dbcc8a5fe0"
        compat.write_text(source.replace(known, "0" * 64), encoding="utf-8")
    return destination


def install_inert_neighbor(fixture: Fixture) -> Path:
    destination = fixture.hermes_home / "plugins" / "inert_neighbor"
    destination.mkdir(parents=True)
    (destination / "plugin.yaml").write_text(
        "name: inert_neighbor\nkind: standalone\nversion: 0.0.0\n",
        encoding="utf-8",
    )
    (destination / "__init__.py").write_text(
        "def _setup(parser):\n"
        "    pass\n\n"
        "def _handle(args):\n"
        "    print('inert-neighbor-ok')\n"
        "    return 0\n\n"
        "def register(ctx):\n"
        "    ctx.register_cli_command(name='inert-neighbor-ping', "
        "help='Inert test neighbor', setup_fn=_setup, handler_fn=_handle)\n",
        encoding="utf-8",
    )
    return destination


def write_config(
    fixture: Fixture,
    *,
    enabled: Iterable[str] = ("a1_task_dispatch",),
    disabled: Iterable[str] = (),
) -> Path:
    enabled_lines = "".join(f"    - {name}\n" for name in enabled) or "    []\n"
    disabled_lines = "".join(f"    - {name}\n" for name in disabled) or "    []\n"
    text = (
        "plugins:\n"
        "  enabled:\n"
        f"{enabled_lines}"
        "  disabled:\n"
        f"{disabled_lines}"
        "kanban:\n"
        "  max_spawn: 4\n"
        "  max_in_progress: 4\n"
    )
    path = fixture.hermes_home / "config.yaml"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    soul = fixture.hermes_home / "SOUL.md"
    soul.write_text("fixture-soul-must-remain-byte-stable\n", encoding="utf-8")
    soul.chmod(0o640)
    return path


def initialize_board(fixture: Fixture, *, assignee: str = "default") -> str:
    code = (
        "from hermes_cli import kanban_db as kb\n"
        "kb._INITIALIZED_PATHS.clear()\n"
        "kb.init_db()\n"
        "with kb.connect() as conn:\n"
        f"    task_id = kb.create_task(conn, title='public-cli-target', assignee={assignee!r})\n"
        "print('TASK_ID=' + task_id)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=isolated_env(fixture),
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"fixture board initialization failed ({result.returncode}):\n{result.stderr}"
        )
    marker = next(
        (line for line in result.stdout.splitlines() if line.startswith("TASK_ID=")),
        None,
    )
    if marker is None:
        raise AssertionError(f"fixture did not emit a task id: {result.stdout!r}")
    return marker.partition("=")[2]


def make_changed_source_checkout(fixture: Fixture, relative_path: str) -> Path:
    """Copy the runtime source and change one module without requiring Git."""
    destination = fixture.root / "changed-source"
    runtime_names = {
        "agent",
        "cron",
        "gateway",
        "hermes_cli",
        "plugins",
        "providers",
        "tools",
    }
    destination.mkdir()
    for source in REPO_ROOT.iterdir():
        if source.is_file() and source.suffix == ".py":
            shutil.copy2(source, destination / source.name)
        elif source.is_dir() and source.name in runtime_names:
            shutil.copytree(
                source,
                destination / source.name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
    target = destination / relative_path
    target.write_bytes(target.read_bytes() + b"\n# changed transitive dependency\n")
    return destination


def run_cli(
    fixture: Fixture, *args: str, repo_root: Path = REPO_ROOT
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "hermes_cli.main"]
    if fixture.profile != "default":
        command.extend(["--profile", fixture.profile])
    command.extend(args)
    return subprocess.run(
        command,
        cwd=repo_root,
        env=isolated_env(fixture, repo_root=repo_root),
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


def tree_snapshot(root: Path) -> dict[str, tuple[str, int, str]]:
    """Snapshot every path's type, permission bits, and file/link content."""
    result: dict[str, tuple[str, int, str]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if path.is_symlink():
            result[relative] = ("symlink", mode, os.readlink(path))
        elif path.is_dir():
            result[relative] = ("dir", mode, "")
        elif path.is_file():
            result[relative] = (
                "file",
                mode,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            result[relative] = ("other", mode, "")
    return result


def logical_db_snapshot(path: Path) -> dict[str, object]:
    """Read every SQLite schema object and table row without opening for write."""
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        schema = [
            tuple(row)
            for row in conn.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
            )
        ]
        tables: dict[str, list[tuple[object, ...]]] = {}
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ):
            quoted = '"' + str(name).replace('"', '""') + '"'
            tables[str(name)] = [
                tuple(row) for row in conn.execute(f"SELECT * FROM {quoted}")
            ]
        return {
            "schema": schema,
            "tables": tables,
            "user_version": conn.execute("PRAGMA user_version").fetchone()[0],
        }
