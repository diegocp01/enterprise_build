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
