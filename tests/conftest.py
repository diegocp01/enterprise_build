from __future__ import annotations

from pathlib import Path

import pytest

from zerohandoff.config import SettingsBundle
from zerohandoff.models import BuildRequest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture()
def settings(repo_root: Path) -> SettingsBundle:
    return SettingsBundle.load(repo_root)


@pytest.fixture()
def build_request() -> BuildRequest:
    return BuildRequest(
        idea="Create a lightweight equipment request tracker",
        audience="Facilities and workplace operations teams",
        outcome="Employees can submit requests and track their status",
        constraints=["React and Vite", "Local only", "No external services"],
        must_have_capabilities=[
            "Create a request",
            "Track request status",
            "Review the operations queue",
        ],
    )
