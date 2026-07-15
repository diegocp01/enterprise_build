from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zerohandoff.config import SettingsBundle
from zerohandoff.models import AgentIdentity, AgentInvocation, ExecutionMode, Personality, Stage
from zerohandoff.runtime.codex import CodexExecAdapter
from zerohandoff.training.puzzles import PuzzleRepository


def _version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    lines = [
        line.strip()
        for line in f"{result.stdout}\n{result.stderr}".splitlines()
        if line.strip() and not line.lower().startswith("warning:")
    ]
    return lines[0] if lines else None


def _live_probe(root: Path, settings: SettingsBundle) -> dict[str, Any]:
    adapter = CodexExecAdapter(root / ".zerohandoff" / "doctor" / "live")
    invocation = AgentInvocation(
        invocation_id=f"gpt56-readiness-{uuid.uuid4().hex[:10]}",
        run_id="doctor_live",
        phase="delivery",
        stage="DOCTOR",
        pair="DOCTOR",
        agent=AgentIdentity(
            name="readiness_probe",
            personality=Personality.CONSCIENTIOUSNESS,
            team=Stage.OBSERVE,
        ),
        mode=ExecutionMode.REVIEW,
        objective="Confirm this schema-constrained Codex runtime probe. Do not use tools or modify files.",
        inputs={"probe": "ZeroHandoff GPT-5.6 readiness"},
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["ready", "runtime"],
            "properties": {
                "ready": {"type": "boolean"},
                "runtime": {"type": "string"},
            },
        },
        model=settings.models["day"]["model"],
        reasoning_effort="low",
        timeout_seconds=120,
        permission_profile="read-only",
        workspace_root=str(root),
    )
    result = adapter.invoke(invocation)
    schema_record = {
        "ok": result.status.value == "completed" and bool((result.output or {}).get("ready")),
        "status": result.status.value,
        "output": result.output,
        "usage": result.usage,
        "raw_trace": result.raw_trace,
        "error": result.error,
    }
    workspace_id = f"workspace-write-{uuid.uuid4().hex[:10]}"
    workspace = root / ".zerohandoff" / "doctor" / "workspace-probe" / workspace_id
    workspace.mkdir(parents=True, exist_ok=False)
    expected = "ZeroHandoff workspace write verified.\n"
    workspace_invocation = AgentInvocation(
        invocation_id=workspace_id,
        run_id="doctor_live",
        phase="delivery",
        stage="DOCTOR",
        pair="DOCTOR",
        agent=AgentIdentity(
            name="workspace_probe",
            personality=Personality.CONSCIENTIOUSNESS,
            team=Stage.EXECUTE,
        ),
        mode=ExecutionMode.WORKSPACE,
        objective=(
            "Write a file named probe.txt in the current workspace containing exactly "
            "'ZeroHandoff workspace write verified.' followed by one newline. Do not create, "
            "read, or modify any other file. Then return the required JSON confirmation."
        ),
        inputs={"allowed_file": "probe.txt"},
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["written", "file"],
            "properties": {
                "written": {"type": "boolean"},
                "file": {"type": "string", "const": "probe.txt"},
            },
        },
        model=settings.models["day"]["model"],
        reasoning_effort="low",
        timeout_seconds=120,
        permission_profile="workspace-write",
        workspace_root=str(workspace),
    )
    workspace_result = adapter.invoke(workspace_invocation)
    files = sorted(
        str(path.relative_to(workspace)) for path in workspace.rglob("*") if path.is_file()
    )
    probe_file = workspace / "probe.txt"
    workspace_record = {
        "ok": (
            workspace_result.status.value == "completed"
            and bool((workspace_result.output or {}).get("written"))
            and files == ["probe.txt"]
            and probe_file.exists()
            and probe_file.read_text() == expected
        ),
        "status": workspace_result.status.value,
        "output": workspace_result.output,
        "files": files,
        "workspace": str(workspace),
        "usage": workspace_result.usage,
        "raw_trace": workspace_result.raw_trace,
        "error": workspace_result.error,
    }
    record = {
        "ok": schema_record["ok"] and workspace_record["ok"],
        "checked_at": datetime.now(UTC).isoformat(),
        "model": invocation.model,
        "schema_probe": schema_record,
        "workspace_probe": workspace_record,
    }
    path = root / ".zerohandoff" / "doctor" / "live_probe.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def doctor(repo_root: Path, *, live: bool = False) -> dict[str, Any]:
    """Return readiness; ``live=True`` also verifies model and sandbox execution."""

    root = repo_root.resolve()
    checks: dict[str, dict[str, Any]] = {}
    settings = None
    try:
        settings = SettingsBundle.load(root)
        checks["settings"] = {"ok": True, "digest": settings.digest}
    except Exception as exc:  # pragma: no cover - exercised through the report contract
        checks["settings"] = {"ok": False, "error": str(exc)}
    for name, command in {
        "node": ["node", "--version"],
        "npm": ["npm", "--version"],
        "ffmpeg": ["ffmpeg", "-version"],
        "ffprobe": ["ffprobe", "-version"],
    }.items():
        version = _version(command)
        checks[name] = {"ok": version is not None, "version": version}
    codex = CodexExecAdapter(root / ".zerohandoff" / "doctor" / "raw").probe()
    checks["codex"] = codex.model_dump(mode="json")
    login = _version(["codex", "login", "status"])
    checks["codex_auth"] = {
        "ok": bool(login and "logged in" in login.lower()),
        "status": login,
    }
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    checks["browser"] = {
        "ok": chrome.exists() or bool(shutil.which("google-chrome") or shutil.which("chromium")),
        "path": str(chrome) if chrome.exists() else None,
    }
    checks["context_guard"] = {
        "ok": (root / "AGENTS.md").exists(),
        "protected_path": str(root / "context"),
    }
    agent_files = sorted((root / ".codex" / "agents").glob("*.toml"))
    skill_files = sorted((root / ".agents" / "skills").glob("*/SKILL.md"))
    checks["codex_project"] = {
        "ok": (
            (root / ".codex" / "config.toml").exists()
            and len(agent_files) == 14
            and len(skill_files) == 3
        ),
        "agents": len(agent_files),
        "skills": len(skill_files),
    }
    try:
        corpus = PuzzleRepository.load(root / "data" / "puzzles.jsonl")
        stats = json.loads((root / "data" / "puzzle_stats.json").read_text())
        checks["puzzle_corpus"] = {
            "ok": (
                len(corpus.puzzles) == 10
                and stats.get("ok") is True
                and stats.get("reproducible") is True
                and stats.get("timeout_verified") is True
                and stats.get("corpus_digest") == corpus.digest
            ),
            "puzzles": len(corpus.puzzles),
            "digest": corpus.digest,
        }
    except Exception as exc:
        checks["puzzle_corpus"] = {"ok": False, "error": str(exc)}
    required = (
        "settings",
        "node",
        "npm",
        "ffmpeg",
        "ffprobe",
        "codex",
        "codex_auth",
        "context_guard",
        "codex_project",
        "puzzle_corpus",
    )
    live_path = root / ".zerohandoff" / "doctor" / "live_probe.json"
    live_result: dict[str, Any] | None = None
    if live:
        if settings is None:
            live_result = {"ok": False, "error": "settings are invalid"}
        else:
            live_result = _live_probe(root, settings)
            if live_result.get("ok") and not checks["codex_auth"]["ok"]:
                checks["codex_auth"] = {
                    "ok": True,
                    "status": "Verified by successful authenticated live probes",
                    "status_command": login,
                }
    elif live_path.exists():
        try:
            live_result = json.loads(live_path.read_text())
        except (OSError, json.JSONDecodeError):
            live_result = {"ok": False, "error": "cached live probe is unreadable"}
    base_ok = all(bool(checks[name].get("ok") or checks[name].get("available")) for name in required)
    return {
        "ok": base_ok and (not live or bool(live_result and live_result.get("ok"))),
        "repo_root": str(root),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "checks": checks,
        "live_model": live_result or {"ok": False, "status": "not_checked"},
        "note": "Authentication is checked locally; GPT-5.6 access is verified by an explicit live probe.",
    }
