"""Runtime release-source inventory for prototype completeness tests.

This test/build helper imports the fixed seed modules in a fresh, isolated
Python process and reports every loaded Python source that originates inside
the release checkout.  It is deliberately independent of the production pin
manifest and of the static import walker.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


RUNTIME_SEED_MODULES = (
    "agent.delegation_context",
    "gateway.lifecycle_ledger",
    "gateway.memory_status",
    "gateway.session_context",
    "hermes_cli.config",
    "hermes_cli.kanban",
    "hermes_cli.kanban_db",
    "hermes_cli.lifecycle",
    "hermes_cli.profiles",
    "hermes_cli.sqlite_safe_read",
    "hermes_cli.sqlite_util",
    "hermes_cli.tools_config",
    "hermes_constants",
    "hermes_state",
    "tools.registry",
    "toolsets",
)


_INVENTORY_SCRIPT = r"""
import importlib
import json
import os
import sys
from pathlib import Path

source_root = Path(os.environ["A1_RUNTIME_SOURCE_ROOT"]).resolve()
seed_modules = json.loads(os.environ["A1_RUNTIME_SEED_MODULES"])

def refuse_external_effects(event, _args):
    if event in {
        "os.system",
        "socket.bind",
        "socket.connect",
        "socket.getaddrinfo",
        "subprocess.Popen",
    }:
        raise RuntimeError(f"runtime inventory refused external effect: {event}")

sys.addaudithook(refuse_external_effects)
for module_name in seed_modules:
    importlib.import_module(module_name)

sources = {}
for module_name, module in tuple(sys.modules.items()):
    raw_origin = getattr(module, "__file__", None)
    if not raw_origin:
        continue
    origin = Path(raw_origin).resolve()
    try:
        relative = origin.relative_to(source_root)
    except ValueError:
        continue
    if relative.parts and relative.parts[0] in {".venv", "venv"}:
        continue
    if origin.suffix != ".py":
        continue
    sources[module_name] = relative.as_posix()

print("A1_RUNTIME_SOURCES=" + json.dumps(sources, sort_keys=True))
"""


def release_runtime_sources(
    source_root: Path,
    sandbox_root: Path,
) -> dict[str, Path]:
    """Return local sources loaded by seed initialization in a fresh process."""
    source_root = source_root.resolve()
    sandbox_root.mkdir(parents=True)
    home = sandbox_root / "home"
    hermes_home = home / ".hermes"
    tmp = sandbox_root / "tmp"
    hermes_home.mkdir(parents=True)
    tmp.mkdir()

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
        "A1_RUNTIME_SEED_MODULES": json.dumps(RUNTIME_SEED_MODULES),
        "A1_RUNTIME_SOURCE_ROOT": str(source_root),
        "HOME": str(home),
        "HERMES_HOME": str(hermes_home),
        "NO_COLOR": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(source_root),
        "PYTHONUTF8": "1",
        "TMPDIR": str(tmp),
    })
    result = subprocess.run(
        [sys.executable, "-c", _INVENTORY_SCRIPT],
        cwd=source_root,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            "runtime source inventory failed "
            f"({result.returncode}):\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    marker = next(
        (
            line.removeprefix("A1_RUNTIME_SOURCES=")
            for line in reversed(result.stdout.splitlines())
            if line.startswith("A1_RUNTIME_SOURCES=")
        ),
        None,
    )
    if marker is None:
        raise AssertionError(
            f"runtime inventory emitted no source map: {result.stdout!r}"
        )
    payload = json.loads(marker)
    if not isinstance(payload, dict) or not all(
        isinstance(name, str) and isinstance(path, str)
        for name, path in payload.items()
    ):
        raise AssertionError("runtime inventory emitted an invalid source map")
    return {name: Path(path) for name, path in sorted(payload.items())}
