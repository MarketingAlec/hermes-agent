"""Fail-closed source admission for the release-pinned prototype."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


EXPECTED_HASHES: Mapping[str, str] = {
    "hermes_cli.kanban": "8e4e51a1403171e706aea60b703eb141e7909615d845efdbc101dfd8cdc6bca0",
    "hermes_cli.kanban_db": "4a4495bb23e5f5c417d4259d759db5acbfc54d9043f1cd46b56724dbcc8a5fe0",
    "hermes_constants": "b78449440ce5149c3896129f9ac309e2ebe70a265b5cae08b4316b7a6910f068",
}


@dataclass(frozen=True)
class Admission:
    ok: bool
    error: str | None
    origins: Mapping[str, Path]


def _module_origin(module_name: str) -> Path:
    spec = importlib.util.find_spec(module_name)
    if spec is None or spec.origin is None:
        raise LookupError(module_name)
    origin = Path(spec.origin).expanduser().resolve()
    loaded = sys.modules.get(module_name)
    loaded_file = getattr(loaded, "__file__", None) if loaded is not None else None
    if loaded_file is not None and Path(loaded_file).expanduser().resolve() != origin:
        raise RuntimeError(f"loaded source origin mismatch: {module_name}")
    return origin


def check_compatibility(
    *,
    origin_resolver: Callable[[str], Path] = _module_origin,
) -> Admission:
    """Verify every directly load-bearing private module without importing it."""
    origins: dict[str, Path] = {}
    for module_name, expected_hash in EXPECTED_HASHES.items():
        try:
            origin = Path(origin_resolver(module_name)).expanduser().resolve()
            digest = hashlib.sha256(origin.read_bytes()).hexdigest()
        except Exception as exc:
            return Admission(
                False, f"source unavailable: {module_name}: {exc}", origins
            )
        origins[module_name] = origin
        if digest != expected_hash:
            return Admission(False, f"source hash mismatch: {module_name}", origins)

    kanban_dir = origins["hermes_cli.kanban"].parent
    if origins["hermes_cli.kanban_db"].parent != kanban_dir:
        return Admission(False, "source origin mismatch: hermes_cli.kanban_db", origins)
    if origins["hermes_constants"].parent != kanban_dir.parent:
        return Admission(False, "source origin mismatch: hermes_constants", origins)
    return Admission(True, None, origins)
