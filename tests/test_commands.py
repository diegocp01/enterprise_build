from __future__ import annotations

import shlex
import sys

import pytest

from zerohandoff.delivery.commands import CommandPolicyError, CommandRunner
from zerohandoff.storage import RunStore


def test_command_runner_enforces_allowlist_workspace_and_timeout(tmp_path) -> None:
    workspace = tmp_path / "app"
    workspace.mkdir()
    store = RunStore(tmp_path / "runs", "delivery_commands", "delivery")
    success = f'{shlex.quote(sys.executable)} -c "print(42)"'
    hanging = f'{shlex.quote(sys.executable)} -c "import time; time.sleep(10)"'
    runner = CommandRunner(
        workspace=workspace,
        allowed_commands={success, hanging},
        store=store,
    )
    assert runner.run(success).passed
    timed = runner.run(hanging, timeout_seconds=1)
    assert timed.timed_out
    assert not timed.passed
    with pytest.raises(CommandPolicyError, match="not allowlisted"):
        runner.run("npm install")

    escape_runner = CommandRunner(
        workspace=workspace,
        allowed_commands={success},
        store=store,
    )
    with pytest.raises(CommandPolicyError, match="escapes"):
        escape_runner.run(success, cwd=tmp_path)
    rows = store.read_jsonl(store.logs_dir / "commands.jsonl")
    assert [row["status"] for row in rows] == ["passed", "failed"]


def test_command_runner_rejects_playwright_false_success_marker(tmp_path) -> None:
    workspace = tmp_path / "app"
    workspace.mkdir()
    store = RunStore(tmp_path / "runs", "false_success", "delivery")
    false_success = (
        f'{shlex.quote(sys.executable)} -c '
        '"print(\'### Error\'); print(\'SyntaxError: Unexpected token\')"'
    )
    ordinary_output = (
        f'{shlex.quote(sys.executable)} -c '
        '"print(\'Error: expected validation copy\')"'
    )
    runner = CommandRunner(
        workspace=workspace,
        allowed_commands={false_success, ordinary_output},
        store=store,
    )

    result = runner.run(false_success)

    assert result.exit_code == 0
    assert result.reported_error == "playwright_cli_error"
    assert result.browser_acceptance_receipt is False
    assert result.passed is False
    assert store.read_jsonl(store.logs_dir / "commands.jsonl")[-1]["status"] == "failed"
    assert len(runner.run_many([false_success, ordinary_output])) == 1
    assert runner.run(ordinary_output).passed is True

    receipt_output = (
        f'{shlex.quote(sys.executable)} -c '
        '"print(\'ZEROHANDOFF_BROWSER_ACCEPTANCE_OK\')"'
    )
    receipt_runner = CommandRunner(
        workspace=workspace,
        allowed_commands={receipt_output},
        store=store,
    )
    receipt = receipt_runner.run(receipt_output)
    assert receipt.passed is True
    assert receipt.browser_acceptance_receipt is True
