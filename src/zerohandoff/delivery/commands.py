from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from zerohandoff.config import digest_value
from zerohandoff.storage import RunStore


class CommandPolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandEvidence:
    command: str
    argv: list[str]
    cwd: str
    exit_code: int
    duration_ms: int
    timed_out: bool
    stdout_digest: str
    stderr_digest: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class CommandRunner:
    """Shell-free, allowlisted process runner with whole-group timeout handling."""

    def __init__(
        self,
        *,
        workspace: Path,
        allowed_commands: set[str],
        store: RunStore | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.allowed_commands = allowed_commands
        self.store = store

    def run(self, command: str, *, cwd: Path | None = None, timeout_seconds: int = 120) -> CommandEvidence:
        if command not in self.allowed_commands:
            raise CommandPolicyError(f"command is not allowlisted: {command}")
        working = (cwd or self.workspace).resolve()
        if working != self.workspace and self.workspace not in working.parents:
            raise CommandPolicyError("command cwd escapes the application workspace")
        argv = shlex.split(command)
        if not argv:
            raise CommandPolicyError("empty commands are not allowed")
        started = time.monotonic()
        process = subprocess.Popen(
            argv,
            cwd=working,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env={**os.environ, "CI": "1", "NO_COLOR": "1"},
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=1)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            stdout, stderr = process.communicate()
        evidence = CommandEvidence(
            command=command,
            argv=argv,
            cwd=str(working),
            exit_code=process.returncode if process.returncode is not None else -1,
            duration_ms=int((time.monotonic() - started) * 1000),
            timed_out=timed_out,
            stdout_digest=digest_value(stdout),
            stderr_digest=digest_value(stderr),
        )
        if self.store is not None:
            self.store.append_log(
                "commands",
                {
                    **evidence.__dict__,
                    "stdout_tail": stdout[-2000:],
                    "stderr_tail": stderr[-2000:],
                    "status": "passed" if evidence.passed else "failed",
                },
            )
        return evidence

    def run_many(self, commands: list[str], *, timeout_seconds: int = 120) -> list[CommandEvidence]:
        results = []
        for command in commands:
            result = self.run(command, timeout_seconds=timeout_seconds)
            results.append(result)
            if not result.passed:
                break
        return results
