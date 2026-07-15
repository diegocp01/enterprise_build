from __future__ import annotations

import shutil
import time
import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

from zerohandoff.api import create_app
from zerohandoff.schemas import SCHEMA_MODELS, export_schemas
from zerohandoff.service import RunService


def copy_runtime_repo(source: Path, destination: Path) -> Path:
    destination.mkdir()
    for name in ("settings", "data"):
        shutil.copytree(source / name, destination / name)
    shutil.copytree(source / "ui" / "dist", destination / "ui" / "dist")
    shutil.copy2(source / "AGENTS.md", destination / "AGENTS.md")
    return destination


def test_project_declares_exactly_fourteen_unique_gpt56_agents_and_three_valid_skills(
    repo_root, settings
) -> None:
    agents = [
        tomllib.loads(path.read_text())
        for path in sorted((repo_root / ".codex/agents").glob("*.toml"))
    ]
    assert len(agents) == 14
    assert len({agent["name"] for agent in agents}) == 14
    assert all(agent["model"] == "gpt-5.6-sol" for agent in agents)
    assert sum(agent["sandbox_mode"] == "workspace-write" for agent in agents) == 2
    assert {path.parent.name for path in (repo_root / ".agents/skills").glob("*/SKILL.md")} == {
        "train-trust",
        "run-pipeline",
        "pipeline-status",
    }
    assert settings.models["day"]["reasoning_effort"] == "medium"
    assert settings.models["night"]["reasoning_effort"] == "xhigh"
    assert settings.models["delivery"]["reasoning_effort"] == "high"
    assert settings.relationship_policy["compiler_version"] == "relationship-policy-v1"
    assert settings.relationship_policy["max_dimensions"] == 3
    control_room = (repo_root / "ui" / "src" / "App.tsx").read_text()
    assert "$run-pipeline" in control_room
    assert "adapter: 'fixture'" not in control_room


def test_schema_export_is_stable_and_complete(tmp_path) -> None:
    first = export_schemas(tmp_path / "one")
    second = export_schemas(tmp_path / "two")
    assert len(first) == len(SCHEMA_MODELS)
    assert [path.read_bytes() for path in first] == [path.read_bytes() for path in second]


def test_control_room_api_starts_and_survives_a_fixture_run(tmp_path, repo_root) -> None:
    root = copy_runtime_repo(repo_root, tmp_path / "runtime-repo")
    service = RunService(root)
    service.train(adapter="fixture", rounds=1, run_id="train_api")
    client = TestClient(create_app(root))
    response = client.post(
        "/api/runs",
        json={
            "adapter": "fixture",
            "run_id": "smoke_api",
            "build_request": {
                "idea": "Create a facilities request board",
                "audience": "Facilities teams",
                "outcome": "Requests are visible and trackable",
                "constraints": ["Local only"],
                "must_have_capabilities": ["Create request", "Track status"],
            },
        },
    )
    assert response.status_code == 202
    deadline = time.monotonic() + 15
    summary = {}
    while time.monotonic() < deadline:
        summary = client.get("/api/runs/smoke_api").json()
        if summary.get("status") in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.05)
    assert summary["status"] == "completed", summary
    assert summary["build_request"]["idea"] == "Create a facilities request board"
    assert any(run["run_id"] == "smoke_api" for run in client.get("/api/runs").json())
    assert client.get("/api/runs/smoke_api/artifacts").status_code == 200
    assert client.get("/api/runs/smoke_api/evidence").json()["gates"]
    bundle = client.get("/api/runs/smoke_api/bundle")
    assert bundle.status_code == 200
    assert bundle.headers["content-type"] == "application/zip"
    assert client.get("/").status_code == 200
