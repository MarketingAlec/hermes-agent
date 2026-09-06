"""Public CLI and loader tests for the disposable local plugin prototype."""

from __future__ import annotations

import json

import pytest

from local_prototypes.a1_task_dispatch.testing.harness import (
    initialize_board,
    install_inert_neighbor,
    install_plugin,
    logical_db_snapshot,
    make_changed_source_checkout,
    make_fixture,
    run_cli,
    tree_snapshot,
    write_config,
)


def _payload(result) -> dict[str, object]:
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, f"no JSON payload; stderr={result.stderr!r}"
    return json.loads(lines[-1])


def test_real_public_cli_discovers_plugin_and_dry_run_is_logically_read_only(tmp_path):
    fixture = make_fixture(tmp_path)
    install_plugin(fixture)
    install_inert_neighbor(fixture)
    write_config(fixture, enabled=("a1_task_dispatch", "inert_neighbor"))
    task_id = initialize_board(fixture)
    before_tree = tree_snapshot(fixture.hermes_home)
    before_db = logical_db_snapshot(fixture.db_path)

    result = run_cli(
        fixture,
        "a1-task-dispatch",
        "--task-id",
        task_id,
        "--dry-run",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = _payload(result)
    assert payload["ok"] is True
    assert payload["task_id"] == task_id
    assert payload["dry_run"] is True
    assert payload["details"]["eligible"] is True
    assert {
        "hermes_cli.kanban",
        "hermes_cli.kanban_db",
        "hermes_constants",
        "gateway.lifecycle_ledger",
        "gateway.memory_status",
    } <= set(payload["release_sources"])
    assert logical_db_snapshot(fixture.db_path) == before_db

    after_tree = tree_snapshot(fixture.hermes_home)
    assert after_tree["config.yaml"] == before_tree["config.yaml"]
    assert after_tree["SOUL.md"] == before_tree["SOUL.md"]
    changed_existing = {
        path
        for path in before_tree.keys() & after_tree.keys()
        if before_tree[path] != after_tree[path]
    }
    assert changed_existing <= {"kanban.db-shm"}
    assert after_tree.keys() - before_tree.keys() <= {
        "kanban.db-shm",
        "kanban.db-wal",
        "kanban.db.dispatch.lock",
        "logs",
        "logs/agent.log",
        "logs/errors.log",
    }

    neighbor = run_cli(fixture, "inert-neighbor-ping")
    assert neighbor.returncode == 0, neighbor.stderr
    assert "inert-neighbor-ok" in neighbor.stdout


@pytest.mark.parametrize("case", ["missing", "disabled", "malformed"])
def test_public_cli_refuses_when_plugin_is_not_loadable(tmp_path, case):
    fixture = make_fixture(tmp_path)
    if case != "missing":
        plugin = install_plugin(fixture)
        if case == "malformed":
            (plugin / "plugin.yaml").write_text(": : : invalid [[[\n", encoding="utf-8")
    write_config(
        fixture,
        enabled=("a1_task_dispatch",),
        disabled=("a1_task_dispatch",) if case == "disabled" else (),
    )
    task_id = initialize_board(fixture)
    before = logical_db_snapshot(fixture.db_path)

    result = run_cli(
        fixture,
        "a1-task-dispatch",
        "--task-id",
        task_id,
        "--dry-run",
        "--json",
    )

    assert result.returncode == 2
    assert "invalid choice: 'a1-task-dispatch'" in result.stderr
    assert logical_db_snapshot(fixture.db_path) == before


def test_public_cli_requires_and_validates_explicit_task_id(tmp_path):
    fixture = make_fixture(tmp_path)
    install_plugin(fixture)
    write_config(fixture)
    task_id = initialize_board(fixture)
    before = logical_db_snapshot(fixture.db_path)

    missing = run_cli(fixture, "a1-task-dispatch", "--dry-run", "--json")
    invalid = run_cli(
        fixture,
        "a1-task-dispatch",
        "--task-id",
        "bad id",
        "--dry-run",
        "--json",
    )

    assert missing.returncode == 2
    assert "the following arguments are required: --task-id" in missing.stderr
    assert invalid.returncode == 2
    assert _payload(invalid)["error"] == "task id must be a non-empty token"
    assert logical_db_snapshot(fixture.db_path) == before
    assert task_id


def test_public_cli_hash_refusal_precedes_board_effects(tmp_path):
    fixture = make_fixture(tmp_path)
    install_plugin(fixture, changed_pin=True)
    write_config(fixture)
    task_id = initialize_board(fixture)
    before_db = logical_db_snapshot(fixture.db_path)
    before_tree = tree_snapshot(fixture.hermes_home)

    result = run_cli(
        fixture,
        "a1-task-dispatch",
        "--task-id",
        task_id,
        "--dry-run",
        "--json",
    )

    assert result.returncode == 2
    assert _payload(result)["error"] == "source hash mismatch: hermes_cli.kanban_db"
    assert logical_db_snapshot(fixture.db_path) == before_db
    after_tree = tree_snapshot(fixture.hermes_home)
    assert "kanban.db.dispatch.lock" not in after_tree
    assert after_tree["config.yaml"] == before_tree["config.yaml"]
    assert after_tree["SOUL.md"] == before_tree["SOUL.md"]


def test_public_cli_refuses_changed_transitive_source_before_board_effects(tmp_path):
    fixture = make_fixture(tmp_path)
    install_plugin(fixture)
    write_config(fixture)
    task_id = initialize_board(fixture)
    changed_source = make_changed_source_checkout(fixture, "gateway/memory_status.py")
    before_db = logical_db_snapshot(fixture.db_path)
    before_tree = tree_snapshot(fixture.hermes_home)

    result = run_cli(
        fixture,
        "a1-task-dispatch",
        "--task-id",
        task_id,
        "--dry-run",
        "--json",
        repo_root=changed_source,
    )

    assert result.returncode == 2
    assert _payload(result)["error"] == ("source hash mismatch: gateway.memory_status")
    assert logical_db_snapshot(fixture.db_path) == before_db
    after_tree = tree_snapshot(fixture.hermes_home)
    assert "kanban.db.dispatch.lock" not in after_tree
    assert after_tree["config.yaml"] == before_tree["config.yaml"]
    assert after_tree["SOUL.md"] == before_tree["SOUL.md"]


def test_public_cli_named_profile_visibility_and_profile_local_refusal(tmp_path):
    worker = make_fixture(tmp_path / "worker-fixture", profile="worker")
    install_plugin(worker)
    write_config(worker)
    task_id = initialize_board(worker)

    visible = run_cli(
        worker,
        "a1-task-dispatch",
        "--task-id",
        task_id,
        "--dry-run",
        "--json",
    )

    assert visible.returncode == 0, visible.stderr
    assert _payload(visible)["ok"] is True

    empty = make_fixture(tmp_path / "empty-fixture", profile="empty")
    write_config(empty, enabled=())
    empty_task = initialize_board(empty)
    refused = run_cli(
        empty,
        "a1-task-dispatch",
        "--task-id",
        empty_task,
        "--dry-run",
        "--json",
    )

    assert refused.returncode == 2
    assert "invalid choice: 'a1-task-dispatch'" in refused.stderr


def test_public_cli_malformed_config_refuses_without_board_mutation(tmp_path):
    fixture = make_fixture(tmp_path)
    install_plugin(fixture)
    write_config(fixture)
    task_id = initialize_board(fixture)
    (fixture.hermes_home / "config.yaml").write_text(
        "plugins:\n  enabled: [a1_task_dispatch]\nkanban: [not-a-mapping]\n",
        encoding="utf-8",
    )
    before = logical_db_snapshot(fixture.db_path)

    result = run_cli(
        fixture,
        "a1-task-dispatch",
        "--task-id",
        task_id,
        "--dry-run",
        "--json",
    )

    assert result.returncode == 2
    assert _payload(result)["error"] == (
        "cannot read dispatch configuration: kanban config must be a mapping"
    )
    assert logical_db_snapshot(fixture.db_path) == before
