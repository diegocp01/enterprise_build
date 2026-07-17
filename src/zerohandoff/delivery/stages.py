from __future__ import annotations

import html
import json
from dataclasses import dataclass
from typing import Any

from zerohandoff.delivery.commands import (
    approved_execute_commands,
    project_command_manifest,
)
from zerohandoff.models import BuildRequest, Stage


@dataclass(frozen=True)
class StageSpec:
    stage: Stage
    artifact_id: str
    artifact_type: str
    title: str
    required_fields: tuple[str, ...]


STAGE_SPECS = {
    Stage.SENSE: StageSpec(
        Stage.SENSE,
        "opportunity_model",
        "opportunity_model",
        "Opportunity Model",
        ("intent", "audience", "desired_outcome", "boundaries", "success_signals", "assumptions"),
    ),
    Stage.MODEL: StageSpec(
        Stage.MODEL,
        "outcome_model",
        "outcome_model",
        "Outcome Model",
        ("contract_items", "quality_bars", "exclusions", "source_links"),
    ),
    Stage.COMPOSE: StageSpec(
        Stage.COMPOSE,
        "capability_graph",
        "capability_graph",
        "Capability Graph",
        ("views", "journey", "states", "accessibility", "visual_blueprint", "flowboard_html"),
    ),
    Stage.DECIDE: StageSpec(
        Stage.DECIDE,
        "decision_graph",
        "decision_graph",
        "Decision Graph",
        ("nodes", "connections", "data", "security", "commands", "diagram_html"),
    ),
    Stage.SIMULATE: StageSpec(
        Stage.SIMULATE,
        "scenario_model",
        "scenario_model",
        "Scenario Model",
        ("work_units", "dependencies", "execution_order", "contract_links"),
    ),
    Stage.EXECUTE: StageSpec(
        Stage.EXECUTE,
        "autonomous_change",
        "autonomous_change",
        "Autonomous Change",
        ("build_steps", "files", "commands", "build_evidence"),
    ),
    Stage.OBSERVE: StageSpec(
        Stage.OBSERVE,
        "evidence_and_learning",
        "evidence_and_learning",
        "Evidence + Learning",
        ("proof_entries", "defects", "demo_plan", "narration_script"),
    ),
}


def contract_item_ids(candidate: dict[str, Any]) -> list[str]:
    ids: set[str] = set()
    for item in candidate.get("contract_items", []):
        if isinstance(item, dict) and item.get("id"):
            ids.add(str(item["id"]))
    for key in ("source_links", "views", "contract_links", "proof_entries", "work_units"):
        for value in candidate.get(key, []):
            if not isinstance(value, dict):
                continue
            direct = value.get("contract_item_id")
            if direct:
                ids.add(str(direct))
            ids.update(str(item) for item in value.get("contract_item_ids", []))
    ids.update(str(item) for item in candidate.get("contract_item_ids", []))
    return sorted(ids)


def stage_files(spec: StageSpec, candidate: dict[str, Any]) -> dict[str, str | dict[str, Any]]:
    markdown = [f"# {spec.title}", ""]
    for key, value in candidate.items():
        markdown.extend([f"## {key.replace('_', ' ').title()}", ""])
        if isinstance(value, str):
            markdown.extend([value, ""])
        else:
            markdown.extend(["```json", json.dumps(value, indent=2, sort_keys=True), "```", ""])
    files: dict[str, str | dict[str, Any]] = {
        f"{spec.artifact_id}.json": candidate,
        f"{spec.artifact_id}.md": "\n".join(markdown).rstrip() + "\n",
    }
    if spec.stage == Stage.DECIDE and candidate.get("diagram_html"):
        files["decision_graph.html"] = str(candidate["diagram_html"])
    if spec.stage == Stage.COMPOSE and candidate.get("flowboard_html"):
        files["capability_graph.html"] = str(candidate["flowboard_html"])
    if spec.stage == Stage.OBSERVE:
        files["demo_plan.json"] = {"steps": candidate.get("demo_plan", [])}
    return files


def _decision_graph_html(name: str) -> str:
    safe_name = html.escape(name)
    return f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_name} — Decision Graph</title>
<style>
body{{margin:0;font:16px system-ui;background:#f6f2e9;color:#0d1720;display:grid;place-items:center;min-height:100vh}}
main{{width:min(920px,90vw)}}h1{{font-size:clamp(2rem,5vw,4rem);margin:0 0 2rem}}
.map{{display:flex;align-items:center;justify-content:center;gap:18px;flex-wrap:wrap}}
.node{{border:2px solid;border-radius:14px;background:#fff;padding:22px 28px;box-shadow:6px 6px #74a1f2;font-weight:800}}
.arrow{{font-size:2rem;font-weight:900}}small{{display:block;margin-top:2rem;color:#66737c}}
</style><main><h1>{safe_name}</h1><div class="map"><div class="node">Browser</div><div class="arrow">→</div><div class="node">React / Vite UI</div><div class="arrow">→</div><div class="node">Typed local state</div></div><small>One local application. No external service dependency.</small></main></html>"""


def _capability_graph_html(name: str) -> str:
    safe_name = html.escape(name)
    return f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_name} — Capability Graph</title>
<style>
body{{margin:0;font:16px system-ui;background:#f6f2e9;color:#0d1720;display:grid;place-items:center;min-height:100vh}}
main{{width:min(960px,90vw)}}h1{{font-size:clamp(2rem,5vw,4rem);margin:0 0 2rem}}
.flow{{display:flex;align-items:stretch;gap:14px;flex-wrap:wrap}}
.card{{flex:1;min-width:170px;border:2px solid;border-radius:14px;background:#fff;padding:20px;box-shadow:6px 6px #f7ca3e}}
.card b{{display:block;margin-bottom:8px}}.card span{{color:#66737c}}
.arrow{{align-self:center;font-size:1.8rem;font-weight:900}}
</style><main><h1>{safe_name}</h1><div class="flow"><div class="card"><b>Open</b><span>Dashboard loads</span></div><div class="arrow">→</div><div class="card"><b>Act</b><span>Review or update a record</span></div><div class="arrow">→</div><div class="card"><b>Confirm</b><span>Visible success state</span></div></div></main></html>"""


def make_fixture_candidate(
    stage: Stage,
    request: BuildRequest,
    previous: dict[Stage, dict[str, Any]],
    *,
    author: str,
) -> dict[str, Any]:
    capabilities = request.must_have_capabilities or [
        "View operational records",
        "Create and update one operational record",
        "Filter records by status",
    ]
    contract_items = [
        {
            "id": f"CAP-{index:03d}",
            "capability": capability,
            "priority": "must",
            "acceptance_checks": [f"A user can {capability.lower()} successfully."],
        }
        for index, capability in enumerate(capabilities, 1)
    ]
    if stage == Stage.SENSE:
        return {
            "intent": request.idea,
            "audience": request.audience,
            "desired_outcome": request.outcome,
            "boundaries": request.constraints or ["Local React/Vite web application"],
            "success_signals": ["Every must-have workflow is demonstrable and proven."],
            "assumptions": ["Seeded local data is acceptable for the hackathon demonstration."],
            "author": author,
        }
    if stage == Stage.MODEL:
        return {
            "contract_items": contract_items,
            "quality_bars": [
                "Responsive from 360px through desktop widths",
                "Keyboard-accessible primary workflow",
                "No external service required",
            ],
            "exclusions": request.out_of_scope or ["Production authentication", "External deployment"],
            "source_links": [
                {"contract_item_id": item["id"], "source": "Build Request"}
                for item in contract_items
            ],
            "author": author,
        }
    outcome_model = previous.get(Stage.MODEL) or make_fixture_candidate(
        Stage.MODEL, request, previous, author=author
    )
    items = outcome_model["contract_items"]
    item_ids = [item["id"] for item in items]
    if stage == Stage.COMPOSE:
        return {
            "views": [
                {
                    "id": "VIEW-001",
                    "name": "Operations Dashboard",
                    "contract_item_ids": item_ids,
                    "purpose": "Complete the primary operational workflow from one view.",
                }
            ],
            "journey": ["Open dashboard", "Review records", "Create or update record", "Confirm"],
            "states": ["loading", "empty", "populated", "validation-error", "success"],
            "accessibility": ["Semantic landmarks", "Visible focus", "Labelled controls", "AA contrast"],
            "visual_blueprint": [
                "[Header: product name] [Summary cards] [Filter controls] [Record table] [Action panel]"
            ],
            "flowboard_html": _capability_graph_html(request.idea),
            "author": author,
        }
    if stage == Stage.DECIDE:
        return {
            "nodes": ["Browser", "React/Vite UI", "Typed local state"],
            "connections": [
                {"from": "Browser", "to": "React/Vite UI"},
                {"from": "React/Vite UI", "to": "Typed local state"},
            ],
            "data": {"storage": "seeded local state", "entities": ["OperationalRecord"]},
            "security": ["No secrets", "No external requests", "Escaped rendered content"],
            "commands": project_command_manifest(),
            "diagram_html": _decision_graph_html(request.idea),
            "contract_item_ids": item_ids,
            "author": author,
        }
    if stage == Stage.SIMULATE:
        work_units = [
            {
                "id": f"UNIT-{index:03d}",
                "contract_item_ids": [item["id"]],
                "objective": item["capability"],
                "done_when": item["acceptance_checks"],
            }
            for index, item in enumerate(items, 1)
        ]
        dependencies = [
            {"before": work_units[index - 1]["id"], "after": work_units[index]["id"]}
            for index in range(1, len(work_units))
        ]
        return {
            "work_units": work_units,
            "dependencies": dependencies,
            "execution_order": [unit["id"] for unit in work_units],
            "contract_links": [
                {"contract_item_id": unit["contract_item_ids"][0], "work_unit_id": unit["id"]}
                for unit in work_units
            ],
            "author": author,
        }
    if stage == Stage.EXECUTE:
        decide_commands = (previous.get(Stage.DECIDE) or {}).get("commands")
        return {
            "build_steps": ["Scaffold React/Vite", "Implement execution graph", "Prove output"],
            "files": [],
            "commands": approved_execute_commands(decide_commands) or {},
            "build_evidence": {},
            "contract_item_ids": item_ids,
            "author": author,
        }
    if stage == Stage.OBSERVE:
        proof_entries = [
            {
                "contract_item_id": item["id"],
                "status": "pass",
                "evidence": ["app-preview", "structural-build-check"],
            }
            for item in items
        ]
        return {
            "proof_entries": proof_entries,
            "defects": [],
            "demo_plan": [
                {
                    "step": 1,
                    "route": "/",
                    "contract_item_ids": item_ids,
                    "expected": "The operational dashboard and primary workflow are visible.",
                    "actions": [
                        {
                            "type": "click",
                            "role": "button",
                            "name": "Start",
                            "value": "advance the first work item",
                        },
                        {
                            "type": "click",
                            "role": "button",
                            "name": "Complete",
                            "value": "complete the same work item",
                        }
                    ],
                }
            ],
            "narration_script": (
                f"This is the finished application created from the request: {request.idea}. "
                "Watch the primary workflow run in a real browser, from its initial input through "
                "the user action and visible result. The final state is backed by the same tests "
                "and evidence included in the delivery bundle."
            ),
            "author": author,
        }
    raise ValueError(f"no fixture candidate for {stage.value}")
