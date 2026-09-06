"""Inert registration for the local A1 exact-task dispatch prototype."""

from __future__ import annotations

import json
import re


def _setup_parser(parser) -> None:
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")


def _handle_command(args) -> int:
    task_id = getattr(args, "task_id", None)
    if not isinstance(task_id, str) or not re.fullmatch(r"[^\s]+", task_id):
        return _emit(
            {
                "ok": False,
                "task_id": task_id,
                "error": "task id must be a non-empty token",
            },
            as_json=bool(getattr(args, "json", False)),
        )

    from .compat import check_compatibility

    admission = check_compatibility()
    if not admission.ok:
        return _emit(
            {"ok": False, "task_id": task_id, "error": admission.error},
            as_json=bool(getattr(args, "json", False)),
        )

    from .adapter import run_command

    return run_command(args, admission=admission)


def _emit(payload: dict[str, object], *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"A1 task dispatch refused: {payload['error']}")
    return 2


def register(ctx) -> None:
    """Register one namespaced CLI command without importing Hermes internals."""
    ctx.register_cli_command(
        name="a1-task-dispatch",
        help="Dispatch one exact ready or review Kanban task",
        setup_fn=_setup_parser,
        handler_fn=_handle_command,
        description=(
            "Release-pinned local prototype for one exact Kanban task. "
            "Refuses unknown Hermes source before opening the board."
        ),
    )
