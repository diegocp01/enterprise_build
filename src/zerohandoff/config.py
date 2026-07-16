from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zerohandoff.models import AgentIdentity, DELIVERY_STAGES, Personality, Stage, TeamDefinition


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_value(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def digest_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class SettingsBundle:
    repo_root: Path
    teams: dict[Stage, TeamDefinition]
    training: dict[str, Any]
    memory: dict[str, Any]
    models: dict[str, Any]
    metaplasticity: dict[str, Any]
    delivery: dict[str, Any]
    continual_learning: dict[str, Any]
    relationship_policy: dict[str, Any]
    digest: str

    @classmethod
    def load(cls, repo_root: Path | None = None) -> SettingsBundle:
        root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
        settings_dir = root / "settings"
        raw = {
            name: json.loads((settings_dir / f"{name}.json").read_text())
            for name in (
                "teams",
                "training",
                "memory",
                "models",
                "metaplasticity",
                "delivery",
                "continual_learning",
                "relationship_policy",
            )
        }
        teams: dict[Stage, TeamDefinition] = {}
        names: set[str] = set()
        for stage_name, members in raw["teams"]["teams"].items():
            stage = Stage(stage_name)
            if stage not in DELIVERY_STAGES:
                raise ValueError(f"unsupported team stage: {stage_name}")
            if len(members) != 2:
                raise ValueError(f"team {stage_name} must contain exactly two agents")
            agents = tuple(
                AgentIdentity(
                    name=member["name"],
                    personality=Personality(member["personality"]),
                    team=stage,
                )
                for member in members
            )
            if any(agent.name in names for agent in agents):
                raise ValueError("agent names must be globally unique")
            names.update(agent.name for agent in agents)
            teams[stage] = TeamDefinition(stage=stage, agents=agents)  # type: ignore[arg-type]
        if set(teams) != set(DELIVERY_STAGES) or len(names) != 14:
            raise ValueError("configuration must define seven teams and 14 agents")
        continual = raw["continual_learning"]
        supported_policies = {
            "expected_success_scope": "directed_edge",
            "handoff_acceptance_policy": "unanimous_receiver_pair",
            "trust_update_scope": "producer_pair_and_receiver_to_producer",
            "terminal_observe_reward": "none",
            "transfer_training_memories": False,
        }
        for key, expected in supported_policies.items():
            if continual.get(key) != expected:
                raise ValueError(
                    f"unsupported continual-learning policy {key}={continual.get(key)!r}; "
                    f"expected {expected!r}"
                )
        digest = digest_value(raw)
        return cls(
            repo_root=root,
            teams=teams,
            training=raw["training"],
            memory=raw["memory"],
            models=raw["models"],
            metaplasticity=raw["metaplasticity"],
            delivery=raw["delivery"],
            continual_learning=raw["continual_learning"],
            relationship_policy=raw["relationship_policy"],
            digest=digest,
        )

    @property
    def agents(self) -> dict[str, AgentIdentity]:
        return {agent.name: agent for team in self.teams.values() for agent in team.agents}

    def snapshot(self) -> dict[str, Any]:
        return {
            "teams": {
                stage.value: [agent.model_dump(mode="json") for agent in team.agents]
                for stage, team in self.teams.items()
            },
            "training": self.training,
            "memory": self.memory,
            "models": self.models,
            "metaplasticity": self.metaplasticity,
            "delivery": self.delivery,
            "continual_learning": self.continual_learning,
            "relationship_policy": self.relationship_policy,
            "digest": self.digest,
        }
