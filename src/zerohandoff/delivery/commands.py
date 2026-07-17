from __future__ import annotations

import os
import re
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


INSTALL_WITHOUT_SCRIPTS_COMMAND = "npm install --ignore-scripts"
PRODUCTION_DEPENDENCY_AUDIT_COMMAND = (
    "npm audit --omit=dev --audit-level=high --json"
)
FULL_DEPENDENCY_AUDIT_COMMAND = "npm audit --audit-level=critical --json"


_PROJECT_COMMAND_MANIFEST = {
    "install": "npm install",
    "test": "npm test",
    "typecheck": "npm run typecheck",
    "build": "npm run build",
    "start": "npm run dev -- --host 127.0.0.1",
    "healthcheck": "http://127.0.0.1:5173/",
}

# Runs created before EXECUTE owned a self-contained command contract used the
# DECIDE fixture's smaller development-command shape. These are exact profiles,
# not a general command allowlist: any extra key or changed value still fails
# closed. A validated legacy profile is normalized to the current manifest.
_LEGACY_DECISION_COMMAND_PROFILES = (
    {
        "install": "npm install",
        "dev": "npm run dev",
        "test": "npm test",
        "build": "npm run build",
    },
    {
        "install": "npm install",
        "dev": "npm run dev",
        "test": "npm test -- --run",
        "build": "npm run build",
    },
)


def project_command_manifest() -> dict[str, str]:
    """Return the single command contract approved for generated applications."""

    return dict(_PROJECT_COMMAND_MANIFEST)


def is_canonical_project_command_manifest(value: object) -> bool:
    """Reject missing, additional, or agent-authored command names and values."""

    return isinstance(value, dict) and value == _PROJECT_COMMAND_MANIFEST


def approved_execute_commands(value: object) -> dict[str, str] | None:
    """Normalize an approved DECIDE command map for EXECUTE, or fail closed.

    New runs must use the canonical manifest. The two exact legacy profiles are
    accepted only so an already-gated run can resume after this contract fix.
    No individual command is accepted by pattern or shell parsing.
    """

    if is_canonical_project_command_manifest(value):
        return project_command_manifest()
    if isinstance(value, dict) and any(
        value == profile for profile in _LEGACY_DECISION_COMMAND_PROFILES
    ):
        return project_command_manifest()
    return None


def execute_validation_commands(
    value: object, *, fixture: bool = False
) -> list[str]:
    """Return the orchestrator-owned, fail-closed EXECUTE validation sequence.

    Dependency audits deliberately live outside the agent-authored project command
    manifest. Production dependencies may not carry high/critical advisories and the
    complete toolchain may not carry critical advisories.
    """

    if fixture:
        if (
            isinstance(value, dict)
            and isinstance(value.get("test"), str)
            and value["test"].strip()
        ):
            return [value["test"]]
        raise CommandPolicyError("fixture command manifest has no test command")
    commands = approved_execute_commands(value)
    if commands is None:
        raise CommandPolicyError("project command manifest is not approved")
    return [
        INSTALL_WITHOUT_SCRIPTS_COMMAND,
        PRODUCTION_DEPENDENCY_AUDIT_COMMAND,
        FULL_DEPENDENCY_AUDIT_COMMAND,
        commands["typecheck"],
        commands["build"],
        # The receipt-owning browser journey must be the final verification step.
        # Nothing may fail after its success marker and leave stale success evidence.
        commands["test"],
    ]


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
    reported_error: str | None
    browser_acceptance_receipt: bool

    @property
    def passed(self) -> bool:
        return (
            self.exit_code == 0
            and not self.timed_out
            and self.reported_error is None
        )


def _reported_output_error(stdout: str, stderr: str) -> str | None:
    """Catch tools that print a structured failure marker but exit successfully.

    In particular, @playwright/cli can emit a ``### Error`` block while returning
    exit code zero. Treating that output as passing would make an acceptance test
    and every downstream proof derived from it false evidence.
    """

    combined = f"{stdout}\n{stderr}"
    if re.search(r"(?m)^### Error[ \t]*\r?$", combined):
        return "playwright_cli_error"
    return None


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
            reported_error=_reported_output_error(stdout, stderr),
            browser_acceptance_receipt=(
                process.returncode == 0
                and not timed_out
                and "ZEROHANDOFF_BROWSER_ACCEPTANCE_OK" in f"{stdout}\n{stderr}"
            ),
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
