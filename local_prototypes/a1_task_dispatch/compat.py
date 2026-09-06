"""Fail-closed source admission for the release-pinned prototype."""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from importlib.machinery import PathFinder
from pathlib import Path
from typing import Callable, Mapping


EXPECTED_HASHES: Mapping[str, str] = {
    # Direct plugin/CLI dependencies.
    "hermes_cli.kanban": "8e4e51a1403171e706aea60b703eb141e7909615d845efdbc101dfd8cdc6bca0",
    "hermes_cli.kanban_db": "4a4495bb23e5f5c417d4259d759db5acbfc54d9043f1cd46b56724dbcc8a5fe0",
    "hermes_constants": "b78449440ce5149c3896129f9ac309e2ebe70a265b5cae08b4316b7a6910f068",
    # kanban_db import-time dependencies.
    "hermes_cli.sqlite_util": "55f41975ed3af6b15c3ca2e2a9dc782b7b99477dd36bfe48d11f462743e56438",
    "toolsets": "31752adbf72647367b1ba31848f8be06001df706d12d1fdcd2fabd38f284de85",
    "tools.registry": "f95d011cae75f0426dd77cb1e25df6bebb61a067d795312df297cb877dd616f5",
    # Claim transaction and file-integrity guards.
    "agent.delegation_context": "d185d1746efa5516a2697dc7e58b1e049300239300475766e91decbfc0c2f754",
    "hermes_cli.sqlite_safe_read": "119a8435496d6136cdb7267a4180158308590ee129a16a1cedd39932323d03cb",
    # Memory-derived capacity and pressure admission.
    "gateway.lifecycle_ledger": "85b7952df35aecf5cc46093019d3e4042e18de28c3fb00eb982ad1b69e49d1c0",
    "gateway.memory_status": "d63fac1fe71761808f95cd1373dacac288a571bc57593a08ef33de783b007f02",
    # Native hook, profile, workspace, and pre-launch preparation.
    "hermes_cli.lifecycle": "e820ed8114d7062d53223113cc053c737d4ced6b624ce1fbe88f79ad8b2a26bd",
    "hermes_cli.profiles": "edeafa558cea28cc42ce4e80a21489a3176454a48bae9f767d8c179b954e0981",
    "gateway.session_context": "b68908ffec7891b12080933a4d50a63d449479a596335c2579776c6e545c4500",
    "hermes_cli.config": "cbce38a2aeed3838dc768d69ee0b31278a211f64bfec66bf786f571c775b3730",
    "hermes_cli.tools_config": "f6edc900cbb16ada9313d004820ae054be84428b1c3bab729555c50a4499bb65",
    "hermes_state": "696fd2d37d66a599228f5b1be5d5b76423b744149c83a2fbd59bf3f45a5f3891",
}

SOURCE_PATHS: Mapping[str, Path] = {
    module_name: Path(*module_name.split(".")).with_suffix(".py")
    for module_name in EXPECTED_HASHES
}


@dataclass(frozen=True)
class Admission:
    ok: bool
    error: str | None
    origins: Mapping[str, Path]


def _module_origin(module_name: str) -> Path:
    """Resolve the source Python would use without importing package parents."""
    search_path = None
    parts = module_name.split(".")
    for index in range(len(parts)):
        qualified = ".".join(parts[: index + 1])
        loaded = sys.modules.get(qualified)
        if loaded is not None:
            spec = getattr(loaded, "__spec__", None)
            loaded_file = getattr(loaded, "__file__", None)
            if spec is None:
                raise LookupError(qualified)
        else:
            spec = PathFinder.find_spec(qualified, search_path)
            loaded_file = None
        if spec is None:
            raise LookupError(qualified)
        if index == len(parts) - 1:
            if spec.origin is None:
                raise LookupError(module_name)
            origin = Path(spec.origin).expanduser().resolve()
            if (
                loaded_file is not None
                and Path(loaded_file).expanduser().resolve() != origin
            ):
                raise RuntimeError(f"loaded source origin mismatch: {module_name}")
            return origin
        locations = (
            getattr(loaded, "__path__", None)
            if loaded is not None
            else spec.submodule_search_locations
        )
        if locations is None:
            raise LookupError(qualified)
        search_path = list(locations)
    raise LookupError(module_name)


def _source_root(module_name: str, origin: Path) -> Path:
    relative = SOURCE_PATHS[module_name]
    root = origin
    for _part in relative.parts:
        root = root.parent
    if (root / relative).resolve() != origin:
        raise RuntimeError(f"source origin mismatch: {module_name}")
    return root


def check_compatibility(
    *,
    origin_resolver: Callable[[str], Path] = _module_origin,
) -> Admission:
    """Verify every load-bearing private module without importing it."""
    origins: dict[str, Path] = {}
    coherent_root: Path | None = None
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
        try:
            root = _source_root(module_name, origin)
        except Exception:
            return Admission(False, f"source origin mismatch: {module_name}", origins)
        if coherent_root is None:
            coherent_root = root
        elif root != coherent_root:
            return Admission(False, f"source origin mismatch: {module_name}", origins)
    return Admission(True, None, origins)
