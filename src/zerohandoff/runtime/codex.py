from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import threading
import time
from pathlib import Path

from jsonschema import ValidationError, validate

from zerohandoff.config import canonical_json
from zerohandoff.models import (
    AgentInvocation,
    AgentResult,
    ExecutionMode,
    InvocationStatus,
    Stage,
)
from zerohandoff.runtime.base import RuntimeCapabilities


class CodexExecAdapter:
    """Codex CLI adapter. It never uses sandbox-bypass flags."""

    name = "codex"
    prompt_only_disabled_features = (
        "shell_tool",
        "unified_exec",
        "apps",
        "multi_agent",
        "browser_use",
        "browser_use_external",
        "browser_use_full_cdp_access",
        "computer_use",
        "image_generation",
        "in_app_browser",
        "plugins",
        "remote_plugin",
        "hooks",
        "goals",
        "memories",
        "workspace_dependencies",
        "standalone_web_search",
    )

    def __init__(self, raw_root: Path, executable: str = "codex") -> None:
        self.executable = executable
        self.raw_root = raw_root.resolve()
        self.raw_root.mkdir(parents=True, exist_ok=True)
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    def probe(self) -> RuntimeCapabilities:
        resolved = shutil.which(self.executable)
        if not resolved:
            return RuntimeCapabilities(
                available=False,
                details={"error": "codex executable not found"},
            )
        try:
            completed = subprocess.run(
                [resolved, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return RuntimeCapabilities(available=False, details={"error": str(exc)})
        version = (completed.stdout or completed.stderr).strip()
        return RuntimeCapabilities(
            available=completed.returncode == 0,
            prompt_execution=True,
            structured_output=True,
            workspace_write=True,
            json_events=True,
            cancellation=True,
            version=version,
        )

    def cancel(self, invocation_id: str) -> bool:
        with self._lock:
            process = self._processes.get(invocation_id)
            if process is None or process.poll() is not None:
                return False
            self._cancelled.add(invocation_id)
        self._terminate_group(process)
        return True

    def cancel_all(self) -> int:
        with self._lock:
            invocation_ids = list(self._processes)
        return sum(self.cancel(invocation_id) for invocation_id in invocation_ids)

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        invocation_dir = self.raw_root / invocation.invocation_id
        if invocation_dir.exists():
            cached = self._cached_result(invocation, invocation_dir)
            if cached is not None:
                return cached
            suffix = f"{invocation.invocation_id}--resume-{int(time.time() * 1000)}"
            invocation_dir = self.raw_root / suffix
        invocation_dir.mkdir(parents=True, exist_ok=False)
        schema_path = invocation_dir / "output.schema.json"
        final_path = invocation_dir / "final.json"
        stdout_path = invocation_dir / "events.jsonl"
        stderr_path = invocation_dir / "stderr.txt"
        schema = invocation.output_schema or {
            "type": "object",
            "additionalProperties": True,
        }
        schema_path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
        prompt = canonical_json(
            {
                "objective": invocation.objective,
                "agent": invocation.agent.model_dump(mode="json"),
                "trust_stance": invocation.trust_stance,
                "relationship_policy": (
                    invocation.relationship_policy.model_dump(
                        mode="json",
                        exclude={"source_vector_digest"},
                    )
                    if invocation.relationship_policy
                    else None
                ),
                "inputs": invocation.inputs,
                "instruction": (
                    "Return only the JSON object required by the supplied output schema."
                ),
            }
        )
        workspace = Path(invocation.workspace_root or ".").resolve()
        command = self._command(
            invocation=invocation,
            schema_path=schema_path,
            final_path=final_path,
            workspace=workspace,
        )
        started = time.monotonic()
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        with self._lock:
            self._processes[invocation.invocation_id] = process
        timeout_seconds = self._effective_timeout_seconds(invocation)
        try:
            stdout, stderr = process.communicate(
                input=prompt,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            self._terminate_group(process)
            stdout, stderr = process.communicate()
            stdout_path.write_text(stdout or exc.stdout or "")
            stderr_path.write_text(stderr or exc.stderr or "")
            return AgentResult(
                invocation_id=invocation.invocation_id,
                status=InvocationStatus.TIMEOUT,
                raw_trace=str(stdout_path),
                duration_ms=int((time.monotonic() - started) * 1000),
                error={"code": "timeout", "message": str(exc)},
            )
        except KeyboardInterrupt:
            # The CLI can be interrupted while a Codex child is in its own process group.
            # Always reap that group so an abandoned retry cannot keep consuming resources.
            self._terminate_group(process)
            stdout, stderr = process.communicate()
            stdout_path.write_text(stdout)
            stderr_path.write_text(stderr)
            raise
        finally:
            with self._lock:
                self._processes.pop(invocation.invocation_id, None)
        stdout_path.write_text(stdout)
        stderr_path.write_text(stderr)
        duration = int((time.monotonic() - started) * 1000)
        with self._lock:
            was_cancelled = invocation.invocation_id in self._cancelled
            self._cancelled.discard(invocation.invocation_id)
        if was_cancelled:
            return AgentResult(
                invocation_id=invocation.invocation_id,
                status=InvocationStatus.CANCELLED,
                raw_trace=str(stdout_path),
                duration_ms=duration,
                error={"code": "cancelled", "message": "invocation cancelled by emergency stop"},
            )
        if process.returncode != 0:
            return AgentResult(
                invocation_id=invocation.invocation_id,
                status=InvocationStatus.BACKEND_ERROR,
                raw_trace=str(stdout_path),
                duration_ms=duration,
                error={
                    "code": "codex_exit_nonzero",
                    "message": f"{stderr[-2000:]}\n{stdout[-2000:]}",
                    "returncode": process.returncode,
                },
            )
        try:
            output = json.loads(final_path.read_text())
            validate(instance=output, schema=schema)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            return AgentResult(
                invocation_id=invocation.invocation_id,
                status=InvocationStatus.INVALID_OUTPUT,
                raw_trace=str(stdout_path),
                duration_ms=duration,
                error={"code": "invalid_structured_output", "message": str(exc)},
            )
        events = []
        usage = None
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
                events.append(event)
                if isinstance(event.get("usage"), dict):
                    usage = event["usage"]
            except json.JSONDecodeError:
                events.append({"type": "unparsed", "text": line})
        return AgentResult(
            invocation_id=invocation.invocation_id,
            status=InvocationStatus.COMPLETED,
            output=output,
            events=events,
            raw_trace=str(stdout_path),
            duration_ms=duration,
            usage=usage,
        )

    def _command(
        self,
        *,
        invocation: AgentInvocation,
        schema_path: Path,
        final_path: Path,
        workspace: Path,
    ) -> list[str]:
        command = [
            self.executable,
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--strict-config",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(final_path),
            "--sandbox",
            invocation.permission_profile,
            "--cd",
            str(workspace),
            "--model",
            invocation.model,
            "--config",
            f'model_reasoning_effort="{invocation.reasoning_effort}"',
            "--config",
            'approval_policy="never"',
        ]
        prompt_only = invocation.mode in {
            ExecutionMode.TRAINING,
            ExecutionMode.CURATION,
            ExecutionMode.ARTIFACT,
        } or (
            invocation.mode == ExecutionMode.REVIEW
            and invocation.stage != Stage.EXECUTE.value
        )
        if prompt_only:
            # All artifact facts live in the typed prompt. Content agents are deliberately
            # prompt-only so tools, unrelated skills, and empty workspaces cannot distort
            # their judgment. EXECUTE workspace implementation and review retain tools.
            for feature in self.prompt_only_disabled_features:
                command.extend(["--disable", feature])
            command.extend(
                [
                    "--config",
                    'web_search="disabled"',
                    "--config",
                    "tools.web_search=false",
                    "--config",
                    (
                        'developer_instructions="This is a prompt-only structured-output '
                        "worker. Do not invoke, inspect, announce, or use any tool, skill, "
                        'plugin, workspace file, or web search. Return one complete JSON '
                        'object matching the supplied output schema."'
                    ),
                ]
            )
            disabled_skills = self._disabled_skills_config(workspace)
            if disabled_skills:
                command.extend(["--config", disabled_skills])
        command.append("-")
        return command

    @staticmethod
    def _effective_timeout_seconds(invocation: AgentInvocation) -> int:
        # Design artifacts can contain a complete standalone HTML flowboard. Keep the
        # approved baseline for every other call, but allow these larger structured
        # responses enough time to finish instead of retrying the same expensive prompt.
        if invocation.mode == ExecutionMode.ARTIFACT:
            return max(invocation.timeout_seconds, 360)
        if invocation.mode == ExecutionMode.WORKSPACE or (
            invocation.mode == ExecutionMode.REVIEW
            and invocation.stage == Stage.EXECUTE.value
        ):
            return max(invocation.timeout_seconds, 600)
        return invocation.timeout_seconds

    @staticmethod
    def _disabled_skills_config(workspace: Path) -> str | None:
        """Disable every discoverable local skill for prompt-only invocations.

        ``--ignore-user-config`` ignores config values, but Codex still discovers user and
        project skills. Typed artifact calls must not activate either source.
        """

        roots = [Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "skills"]
        for directory in (workspace, *workspace.parents):
            roots.extend((directory / ".agents" / "skills", directory / ".codex" / "skills"))
        skill_paths = sorted(
            {
                skill_file.parent.resolve()
                for root in roots
                if root.is_dir()
                for skill_file in root.rglob("SKILL.md")
            },
            key=str,
        )
        if not skill_paths:
            return None
        entries = ", ".join(
            f'{{ path = {json.dumps(str(path))}, enabled = false }}'
            for path in skill_paths
        )
        return f"skills.config=[{entries}]"

    @staticmethod
    def _terminate_group(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=1)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    @staticmethod
    def _cached_result(invocation: AgentInvocation, invocation_dir: Path) -> AgentResult | None:
        final_path = invocation_dir / "final.json"
        stdout_path = invocation_dir / "events.jsonl"
        if not final_path.exists() or not stdout_path.exists():
            return None
        schema = invocation.output_schema or {"type": "object", "additionalProperties": True}
        try:
            output = json.loads(final_path.read_text())
            validate(instance=output, schema=schema)
        except (OSError, json.JSONDecodeError, ValidationError):
            return None
        events = []
        usage = None
        for line in stdout_path.read_text().splitlines():
            try:
                event = json.loads(line)
                events.append(event)
                if isinstance(event.get("usage"), dict):
                    usage = event["usage"]
            except json.JSONDecodeError:
                events.append({"type": "unparsed", "text": line})
        return AgentResult(
            invocation_id=invocation.invocation_id,
            status=InvocationStatus.COMPLETED,
            output=output,
            events=events,
            raw_trace=str(stdout_path),
            duration_ms=0,
            usage=usage,
            resume_token="cached-completed-invocation",
        )
