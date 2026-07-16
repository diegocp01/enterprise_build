from __future__ import annotations

from zerohandoff.models import AgentInvocation, ExecutionMode
from zerohandoff.runtime.codex import CodexExecAdapter


def _invocation(settings, *, mode: ExecutionMode) -> AgentInvocation:
    agent = next(iter(settings.agents.values()))
    phase = (
        "training"
        if mode in {ExecutionMode.TRAINING, ExecutionMode.CURATION}
        else "delivery"
    )
    return AgentInvocation(
        invocation_id=f"test-{mode.value}",
        run_id="test-runtime",
        phase=phase,
        stage=agent.team.value,
        pair=agent.team.value,
        agent=agent,
        mode=mode,
        objective="Return the structured answer.",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
    )


def test_non_workspace_content_commands_are_prompt_only(settings, tmp_path) -> None:
    adapter = CodexExecAdapter(tmp_path / "raw")
    for mode in (
        ExecutionMode.TRAINING,
        ExecutionMode.CURATION,
        ExecutionMode.ARTIFACT,
    ):
        command = adapter._command(
            invocation=_invocation(settings, mode=mode),
            schema_path=tmp_path / "schema.json",
            final_path=tmp_path / "final.json",
            workspace=tmp_path,
        )
        disabled = {
            command[index + 1]
            for index, value in enumerate(command)
            if value == "--disable"
        }
        assert disabled == set(adapter.prompt_only_disabled_features)
        assert "browser_use" in disabled
        assert "standalone_web_search" in disabled
        assert "--ignore-user-config" in command
        assert "--strict-config" in command
        assert "--sandbox" in command and "read-only" in command
        configs = {
            command[index + 1]
            for index, value in enumerate(command)
            if value == "--config"
        }
        assert 'web_search="disabled"' in configs
        assert "tools.web_search=false" in configs
        assert any(config.startswith("developer_instructions=") for config in configs)
        assert any(config.startswith("skills.config=[") for config in configs)


def test_delivery_workspace_command_keeps_tools_available(settings, tmp_path) -> None:
    adapter = CodexExecAdapter(tmp_path / "raw")
    command = adapter._command(
        invocation=_invocation(settings, mode=ExecutionMode.WORKSPACE),
        schema_path=tmp_path / "schema.json",
        final_path=tmp_path / "final.json",
        workspace=tmp_path,
    )
    assert "--disable" not in command
    assert 'web_search="disabled"' not in command
    assert "tools.web_search=false" not in command


def test_large_artifacts_get_extended_timeout(settings, tmp_path) -> None:
    adapter = CodexExecAdapter(tmp_path / "raw")
    artifact = _invocation(settings, mode=ExecutionMode.ARTIFACT)
    workspace = _invocation(settings, mode=ExecutionMode.WORKSPACE)
    execute_review = _invocation(settings, mode=ExecutionMode.REVIEW).model_copy(
        update={"stage": "EXECUTE"}
    )
    content_review = _invocation(settings, mode=ExecutionMode.REVIEW)
    assert adapter._effective_timeout_seconds(artifact) == 360
    assert adapter._effective_timeout_seconds(workspace) == 600
    assert adapter._effective_timeout_seconds(execute_review) == 600
    assert (
        adapter._effective_timeout_seconds(content_review)
        == content_review.timeout_seconds
    )
