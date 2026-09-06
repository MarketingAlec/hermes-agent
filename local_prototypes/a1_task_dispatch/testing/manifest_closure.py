"""Independent release-source closure calculation for prototype tests.

This module is test/build tooling, not part of plugin admission.  It derives
the source files Python can execute while importing the release-fixed seed
modules without importing those modules itself.
"""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path


RELEASE_SEED_MODULES = frozenset({
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
})


def _module_source(root: Path, module_name: str) -> Path | None:
    stem = root.joinpath(*module_name.split("."))
    package_source = stem / "__init__.py"
    if package_source.is_file():
        return package_source
    module_source = stem.with_suffix(".py")
    return module_source if module_source.is_file() else None


def _relative_import_base(
    module_name: str,
    source: Path,
    node: ast.ImportFrom,
) -> str:
    if node.level == 0:
        return node.module or ""
    package_parts = module_name.split(".")
    if source.name != "__init__.py":
        package_parts = package_parts[:-1]
    keep = max(0, len(package_parts) - node.level + 1)
    parts = package_parts[:keep]
    if node.module:
        parts.extend(node.module.split("."))
    return ".".join(parts)


def _resolved_imports(
    root: Path,
    module_name: str,
    source: Path,
    node: ast.Import | ast.ImportFrom,
) -> set[str]:
    if isinstance(node, ast.Import):
        candidates = {alias.name for alias in node.names}
    else:
        base = _relative_import_base(module_name, source, node)
        candidates = {base} if base else set()
        for alias in node.names:
            candidate = f"{base}.{alias.name}" if base else alias.name
            candidates.add(candidate)
    return {name for name in candidates if _module_source(root, name) is not None}


class _ExecutedCodeVisitor(ast.NodeVisitor):
    """Collect imports/calls while leaving deferred function bodies alone."""

    def __init__(self) -> None:
        self.imports: list[ast.Import | ast.ImportFrom] = []
        self.calls: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.append(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.append(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            self.calls.add(node.func.id)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_definition(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # Its defaults are represented on the containing Call/assignment;
        # the lambda body itself is deferred.
        return

    def _visit_function_definition(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        # Decorators, defaults and annotations execute when the definition is
        # created; the function or method body does not.
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.args.vararg and node.args.vararg.annotation is not None:
            self.visit(node.args.vararg.annotation)
        if node.args.kwarg and node.args.kwarg.annotation is not None:
            self.visit(node.args.kwarg.annotation)
        if node.returns is not None:
            self.visit(node.returns)


def _module_imports(root: Path, module_name: str, source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    helpers = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    visitor = _ExecutedCodeVisitor()
    for statement in tree.body:
        visitor.visit(statement)

    imports = list(visitor.imports)
    pending_helpers = deque(visitor.calls & helpers.keys())
    visited_helpers: set[str] = set()
    while pending_helpers:
        helper_name = pending_helpers.popleft()
        if helper_name in visited_helpers:
            continue
        visited_helpers.add(helper_name)
        helper_visitor = _ExecutedCodeVisitor()
        for statement in helpers[helper_name].body:
            helper_visitor.visit(statement)
        imports.extend(helper_visitor.imports)
        pending_helpers.extend(
            (helper_visitor.calls & helpers.keys()) - visited_helpers
        )

    result: set[str] = set()
    for node in imports:
        result.update(_resolved_imports(root, module_name, source, node))
    return result


def release_manifest_closure(root: Path) -> dict[str, Path]:
    """Return the finite module-initialization source closure of the seeds."""
    root = root.resolve()
    pending = deque(sorted(RELEASE_SEED_MODULES))
    sources: dict[str, Path] = {}
    while pending:
        module_name = pending.popleft()
        if module_name in sources:
            continue
        source = _module_source(root, module_name)
        if source is None:
            raise AssertionError(f"release source is missing: {module_name}")
        sources[module_name] = source.relative_to(root)

        parts = module_name.split(".")
        for length in range(1, len(parts)):
            package_name = ".".join(parts[:length])
            if package_name not in sources:
                pending.append(package_name)
        pending.extend(sorted(_module_imports(root, module_name, source)))
    return dict(sorted(sources.items()))
