from __future__ import annotations

import re
from typing import Any

from zerohandoff.delivery.commands import is_canonical_project_command_manifest
from zerohandoff.delivery.stages import STAGE_SPECS, contract_item_ids
from zerohandoff.models import BuildRequest, GateDecision, GateResult, ReviewFinding, Stage


DEMO_ACCESSIBLE_ROLES = {
    "", "alert", "alertdialog", "application", "article", "banner", "blockquote",
    "button", "caption", "cell", "checkbox", "code", "columnheader", "combobox",
    "complementary", "contentinfo", "definition", "deletion", "dialog", "directory",
    "document", "emphasis", "feed", "figure", "form", "generic", "grid", "gridcell",
    "group", "heading", "img", "insertion", "link", "list", "listbox", "listitem",
    "log", "main", "marquee", "math", "meter", "menu", "menubar", "menuitem",
    "menuitemcheckbox", "menuitemradio", "navigation", "none", "note", "option",
    "paragraph", "presentation", "progressbar", "radio", "radiogroup", "region", "row",
    "rowgroup", "rowheader", "scrollbar", "search", "searchbox", "separator", "slider",
    "spinbutton", "status", "strong", "subscript", "superscript", "switch", "tab",
    "table", "tablist", "tabpanel", "term", "textbox", "time", "timer", "toolbar",
    "tooltip", "tree", "treegrid", "treeitem",
}


class GateEngine:
    ALLOW_EMPTY = {
        (Stage.MODEL, "exclusions"),
        (Stage.SIMULATE, "dependencies"),
        (Stage.OBSERVE, "defects"),
    }
    SECRET_PATTERN = re.compile(
        r"(?:sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]+PRIVATE KEY-----|"
        r"(?:password|secret|api[_ -]?key)\s*[:=]\s*\S+)",
        re.IGNORECASE,
    )
    OUT_OF_PROFILE_PATTERN = re.compile(
        r"\b(?:deploy to production|publish publicly|make a purchase|send customer emails?)\b",
        re.IGNORECASE,
    )

    def evaluate_intake(self, request: BuildRequest) -> GateResult:
        payload = request.model_dump(mode="json")
        flattened = "\n".join(self._strings(payload))
        rule_results = {
            "intake.required.idea": bool(request.idea.strip()),
            "intake.required.audience": bool(request.audience.strip()),
            "intake.required.outcome": bool(request.outcome.strip()),
            "intake.no_embedded_secrets": self.SECRET_PATTERN.search(flattened) is None,
            "intake.local_autonomous_scope": self.OUT_OF_PROFILE_PATTERN.search(flattened) is None,
        }
        findings = [
            ReviewFinding(
                rule_id=rule_id,
                severity="critical" if "secret" in rule_id or "scope" in rule_id else "high",
                message=f"Build Request failed intake rule: {rule_id}.",
            )
            for rule_id, passed in rule_results.items()
            if not passed
        ]
        return GateResult(
            stage=Stage.INTAKE,
            decision=GateDecision.PASS if not findings else GateDecision.BLOCKED,
            rule_results=rule_results,
            findings=findings,
            evidence=["build_request"],
        )

    @classmethod
    def _strings(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [item for nested in value.values() for item in cls._strings(nested)]
        if isinstance(value, list):
            return [item for nested in value for item in cls._strings(nested)]
        return []

    def evaluate(
        self,
        stage: Stage,
        candidate: dict[str, Any],
        peer_findings: list[ReviewFinding],
        *,
        expected_contract_ids: list[str] | None = None,
        valid_evidence_refs: set[str] | None = None,
    ) -> GateResult:
        spec = STAGE_SPECS[stage]
        rule_results = {}
        for field in spec.required_fields:
            present = field in candidate and candidate[field] is not None
            if present and (stage, field) not in self.ALLOW_EMPTY:
                present = bool(candidate[field])
            rule_results[f"{stage.value.lower()}.required.{field}"] = present
        findings = list(peer_findings)
        for rule_id, passed in rule_results.items():
            if not passed:
                findings.append(
                    ReviewFinding(
                        rule_id=rule_id,
                        severity="high",
                        message=f"Required stage field failed: {rule_id.rsplit('.', 1)[-1]}",
                    )
                )
        if expected_contract_ids is not None:
            traceability_exact = set(contract_item_ids(candidate)) == set(
                expected_contract_ids
            )
            rule_results[f"{stage.value.lower()}.contract_traceability_exact"] = (
                traceability_exact
            )
            if not traceability_exact:
                findings.append(
                    ReviewFinding(
                        rule_id=(
                            f"{stage.value.lower()}.contract_traceability_exact"
                        ),
                        severity="critical",
                        message=(
                            "The stage must preserve exactly every Outcome Model contract ID; "
                            "IDs may not be dropped, invented, or silently restored later."
                        ),
                        evidence=[
                            f"expected={','.join(sorted(expected_contract_ids))}",
                            f"actual={','.join(contract_item_ids(candidate))}",
                        ],
                    )
                )
        if stage == Stage.OBSERVE:
            ledger = candidate.get("proof_entries", [])
            passing_statuses = {"pass", "passed"}
            all_proven = bool(ledger) and all(
                str(row.get("status", "")).strip().lower() in passing_statuses
                for row in ledger
                if isinstance(row, dict)
            ) and all(isinstance(row, dict) for row in ledger)
            rule_results["observe.must_have_evidenced"] = all_proven
            if not all_proven:
                findings.append(
                    ReviewFinding(
                        rule_id="observe.must_have_evidenced",
                        severity="critical",
                        message="Every must-have outcome item needs passing evidence.",
                    )
                )
            if expected_contract_ids is not None:
                proof_ids = {
                    str(row.get("contract_item_id"))
                    for row in ledger
                    if isinstance(row, dict) and row.get("contract_item_id")
                }
                exact_proof_coverage = proof_ids == set(expected_contract_ids)
                rule_results["observe.contract_coverage_exact"] = exact_proof_coverage
                if not exact_proof_coverage:
                    findings.append(
                        ReviewFinding(
                            rule_id="observe.contract_coverage_exact",
                            severity="critical",
                            message="Proof entries must cover every Outcome Model ID exactly.",
                        )
                    )
            if valid_evidence_refs is not None:
                evidence_refs = [
                    str(reference)
                    for row in ledger
                    if isinstance(row, dict)
                    for reference in row.get("evidence", [])
                ]
                evidence_resolves = bool(evidence_refs) and all(
                    reference in valid_evidence_refs for reference in evidence_refs
                ) and all(
                    bool(row.get("evidence"))
                    for row in ledger
                    if isinstance(row, dict)
                )
                rule_results["observe.evidence_refs_resolve"] = evidence_resolves
                if not evidence_resolves:
                    findings.append(
                        ReviewFinding(
                            rule_id="observe.evidence_refs_resolve",
                            severity="critical",
                            message=(
                                "Every proof entry must cite recorded command or file-digest "
                                "evidence that resolves inside this run."
                            ),
                        )
                    )
            demo_plan = candidate.get("demo_plan", [])
            demo_actions = [
                action
                for step in demo_plan
                if isinstance(step, dict)
                for action in step.get("actions", [])
                if isinstance(action, dict)
            ]
            mutating_demo_actions = [
                action
                for action in demo_actions
                if action.get("type") in {"click", "select", "fill"}
            ]
            executable_demo = bool(demo_plan) and all(
                isinstance(step, dict)
                and bool(str(step.get("expected", "")).strip())
                and bool(step.get("actions"))
                and all(
                    isinstance(action, dict)
                    and action.get("type") in {"click", "select", "fill", "scroll", "wait"}
                    and all(field in action for field in ("role", "name", "value"))
                    and str(action.get("role", "")) in DEMO_ACCESSIBLE_ROLES
                    and (
                        action.get("type") != "wait"
                        or str(action.get("value", "")).strip().isdigit()
                    )
                    for action in step.get("actions", [])
                )
                for step in demo_plan
            ) and len(mutating_demo_actions) >= 2
            presenter_copy = len(str(candidate.get("narration_script", "")).split()) >= 20
            rule_results["observe.demo_plan_executable"] = executable_demo
            rule_results["observe.narration_presenter_ready"] = presenter_copy
            if not executable_demo:
                findings.append(
                    ReviewFinding(
                        rule_id="observe.demo_plan_executable",
                        severity="high",
                        message=(
                            "The demo plan needs an expected state for every step and at least "
                            "two accessible click, select, or fill actions. Supported actions "
                            "are click/select/fill/scroll/wait, and wait values must be numeric "
                            "milliseconds with observable text in name or selector. Roles must "
                            "be real Playwright ARIA roles; use an empty role for visible text "
                            "and the selector field for CSS."
                        ),
                    )
                )
            if not presenter_copy:
                findings.append(
                    ReviewFinding(
                        rule_id="observe.narration_presenter_ready",
                        severity="high",
                        message="The narration must be polished presenter copy of at least 20 words.",
                    )
                )
        if stage == Stage.MODEL:
            contract_items = candidate.get("contract_items", [])
            ids = [row.get("id") for row in contract_items if isinstance(row, dict)]
            testable = bool(ids) and len(ids) == len(set(ids)) and all(
                row.get("acceptance_checks") for row in contract_items if isinstance(row, dict)
            )
            rule_results["model.outcome_items_testable_unique"] = testable
            if not testable:
                findings.append(
                    ReviewFinding(
                        rule_id="model.outcome_items_testable_unique",
                        severity="high",
                        message="Outcome Model items need unique IDs and acceptance checks.",
                    )
                )
        if stage == Stage.DECIDE:
            approved_commands = is_canonical_project_command_manifest(
                candidate.get("commands")
            )
            rule_results["decide.commands_approved"] = approved_commands
            if not approved_commands:
                findings.append(
                    ReviewFinding(
                        rule_id="decide.commands_approved",
                        severity="critical",
                        message=(
                            "DECIDE commands must exactly match the generated-app command "
                            "policy; arbitrary names, values, omissions, and additions are "
                            "not executable authority."
                        ),
                    )
                )
        if stage == Stage.EXECUTE:
            approved_commands = is_canonical_project_command_manifest(
                candidate.get("commands")
            )
            rule_results["execute.command_contract_approved"] = approved_commands
            if not approved_commands:
                findings.append(
                    ReviewFinding(
                        rule_id="execute.command_contract_approved",
                        severity="critical",
                        message=(
                            "EXECUTE must carry the canonical command contract hydrated "
                            "from the approved DECIDE map."
                        ),
                    )
                )
            build_evidence = candidate.get("build_evidence", {})
            verified = bool(build_evidence.get("passed")) and bool(
                build_evidence.get("command_results")
            )
            rule_results["execute.commands_verified"] = verified
            if not verified:
                findings.append(
                    ReviewFinding(
                        rule_id="execute.commands_verified",
                        severity="critical",
                        message="EXECUTE requires successful recorded command evidence.",
                    )
                )
        if stage == Stage.SIMULATE:
            work_units = candidate.get("work_units", [])
            unit_ids = [row.get("id") for row in work_units if isinstance(row, dict)]
            dependencies = candidate.get("dependencies", [])
            execution_order = candidate.get("execution_order", [])
            graph = {unit_id: set() for unit_id in unit_ids if unit_id}
            valid_edges = True
            for edge in dependencies:
                if not isinstance(edge, dict):
                    valid_edges = False
                    continue
                before = edge.get("before")
                after = edge.get("after")
                if before not in graph or after not in graph:
                    valid_edges = False
                    continue
                graph[before].add(after)

            visiting: set[str] = set()
            visited: set[str] = set()
            positions = {unit_id: index for index, unit_id in enumerate(execution_order)}

            def visit(node: str) -> bool:
                if node in visiting:
                    return False
                if node in visited:
                    return True
                visiting.add(node)
                valid = all(visit(neighbor) for neighbor in graph[node])
                visiting.remove(node)
                visited.add(node)
                return valid

            valid_graph = (
                bool(unit_ids)
                and len(unit_ids) == len(set(unit_ids))
                and set(execution_order) == set(unit_ids)
                and len(execution_order) == len(unit_ids)
                and valid_edges
                and all(
                    positions[before] < positions[after]
                    for before, afters in graph.items()
                    for after in afters
                )
                and all(visit(node) for node in graph)
            )
            rule_results["simulate.scenario_model_valid"] = valid_graph
            if not valid_graph:
                findings.append(
                    ReviewFinding(
                        rule_id="simulate.scenario_model_valid",
                        severity="high",
                        message="Scenario Model needs unique work units and a valid acyclic order.",
                    )
                )
        blocking = [finding for finding in findings if finding.severity in {"critical", "high"}]
        decision = GateDecision.REPAIR if blocking else GateDecision.PASS
        return GateResult(
            stage=stage,
            decision=decision,
            rule_results=rule_results,
            findings=findings,
            evidence=[f"candidate:{stage.value.lower()}"],
        )
