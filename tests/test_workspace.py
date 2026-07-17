from __future__ import annotations

import json

import pytest

from zerohandoff.delivery.workspace import (
    BROWSER_ACCEPTANCE_HARNESS_REQUIREMENTS,
    ReactViteWorkspaceBuilder,
)


def test_browser_harness_contract_covers_experiment_three_failure_modes() -> None:
    requirements = BROWSER_ACCEPTANCE_HARNESS_REQUIREMENTS

    assert "build the current production bundle" in requirements
    assert "exactly one keyDown with text and unmodifiedText set to `\\r`" in requirements
    assert "non-text keys such as Tab or Escape as rawKeyDown followed by keyUp" in requirements
    assert "windowsVirtualKeyCode, code, key, location, and modifiers" in requirements
    assert "omit nativeVirtualKeyCode" in requirements
    assert "never fire-and-forget" in requirements
    assert "never" in requirements and "separate char event" in requirements
    assert "document.activeElement is that exact element" in requirements
    assert "body-text match is not focus" in requirements
    assert "Repeated exports that reuse a deterministic filename" in requirements
    assert "correlate the new completed download" in requirements
    assert "overall journey" in requirements and "bounded" in requirements
    assert "fetch callback must carry its own AbortSignal or AbortController" in requirements
    assert "outer polling deadline is not a substitute" in requirements
    assert "matches :focus-visible" in requirements
    assert "nonzero computed outline, box-shadow" in requirements
    assert "activeElement equality alone is insufficient" in requirements
    assert "child processes stopped in finally" in requirements
    assert "ZEROHANDOFF_BROWSER_ACCEPTANCE_OK only after" in requirements


def test_external_service_check_ignores_generated_vite_bundle(
    build_request, tmp_path
) -> None:
    builder = ReactViteWorkspaceBuilder()
    workspace = tmp_path / "app"
    implementation = {"contract_item_ids": ["MODEL-01"]}
    evidence = builder.build(workspace, build_request, implementation)
    assert (workspace / "src" / "vite-env.d.ts").exists()

    assets = workspace / "dist" / "assets"
    assets.mkdir(parents=True)
    (assets / "vite-runtime.js").write_text("fetch(modulePreload.href)\n")

    inspected = builder.inspect(
        workspace,
        implementation,
        commands=evidence.commands,
    )
    assert inspected.checks["no_external_services"] is True

    (workspace / "src" / "network.ts").write_text("fetch('/api/requests')\n")
    inspected = builder.inspect(
        workspace,
        implementation,
        commands=evidence.commands,
    )
    assert inspected.checks["no_external_services"] is False


@pytest.mark.parametrize("broken", [False, True])
def test_workspace_rejects_top_level_node_modules_symlink(
    build_request, tmp_path, broken
) -> None:
    builder = ReactViteWorkspaceBuilder()
    workspace = tmp_path / "app"
    implementation = {"contract_item_ids": ["MODEL-01"]}
    evidence = builder.build(workspace, build_request, implementation)
    target = tmp_path / "dependencies"
    if not broken:
        target.mkdir()
    (workspace / "node_modules").symlink_to(target, target_is_directory=True)

    inspected = builder.inspect(workspace, implementation, commands=evidence.commands)

    assert inspected.checks["node_module_links_local"] is False


def test_workspace_rejects_agent_rewritten_validation_script(
    build_request, tmp_path
) -> None:
    builder = ReactViteWorkspaceBuilder()
    workspace = tmp_path / "app"
    implementation = {"contract_item_ids": ["MODEL-01"]}
    evidence = builder.build(workspace, build_request, implementation)
    package_path = workspace / "package.json"
    package = json.loads(package_path.read_text())
    package["scripts"]["test"] = "node -e process.env"
    package_path.write_text(json.dumps(package, indent=2) + "\n")

    inspected = builder.inspect(workspace, implementation, commands=evidence.commands)

    assert inspected.checks["package_scripts_allowlisted"] is False


def test_workspace_scaffold_is_pinned_locked_and_portable(
    build_request, tmp_path
) -> None:
    builder = ReactViteWorkspaceBuilder()
    workspace = tmp_path / "app"
    implementation = {"contract_item_ids": ["MODEL-01"]}
    evidence = builder.build(workspace, build_request, implementation)

    assert "base: './'" in (workspace / "vite.config.ts").read_text()
    assert evidence.checks["dependencies_exactly_pinned"] is True
    assert evidence.checks["package_lock_present"] is True
    assert evidence.checks["package_lock_matches_manifest"] is True
    assert evidence.checks["portable_dist_assets"] is True


def test_workspace_rejects_undeclared_acceptance_harness(
    build_request, tmp_path
) -> None:
    builder = ReactViteWorkspaceBuilder()
    workspace = tmp_path / "app"
    implementation = {"contract_item_ids": ["MODEL-01"]}
    evidence = builder.build(workspace, build_request, implementation)
    (workspace / "tests" / "browser.acceptance.sh").write_text(
        "#!/bin/sh\nexit 0\n"
    )

    inspected = builder.inspect(workspace, implementation, commands=evidence.commands)

    assert inspected.checks["test_harnesses_declared"] is False


def test_workspace_accepts_allowlisted_required_browser_harness(
    build_request, tmp_path
) -> None:
    builder = ReactViteWorkspaceBuilder()
    workspace = tmp_path / "app"
    implementation = {"contract_item_ids": ["MODEL-01"]}
    evidence = builder.build(workspace, build_request, implementation)
    browser_js = workspace / "tests" / "browser.acceptance.js"
    browser_sh = workspace / "tests" / "browser.acceptance.sh"
    browser_js.write_text("console.log('verified')\n")
    browser_sh.write_text("playwright-cli --browser chrome run-code tests/browser.acceptance.js\n")
    package_path = workspace / "package.json"
    package = json.loads(package_path.read_text())
    package["scripts"]["test"] = (
        "vitest run && bash tests/browser.acceptance.sh"
    )
    package_path.write_text(json.dumps(package, indent=2) + "\n")

    inspected = builder.inspect(workspace, implementation, commands=evidence.commands)

    assert inspected.checks["package_scripts_allowlisted"] is True
    assert inspected.checks["test_harnesses_declared"] is True
    assert inspected.checks["browser_acceptance_receipt_verified"] is False
    assert inspected.checks["browser_runtime_prerequisites_documented"] is False

    package["scripts"]["test"] = "bash tests/browser.acceptance.sh"
    package_path.write_text(json.dumps(package, indent=2) + "\n")
    wrapper_entrypoint = builder.inspect(
        workspace, implementation, commands=evidence.commands
    )
    assert wrapper_entrypoint.checks["package_scripts_allowlisted"] is True
    assert wrapper_entrypoint.checks["test_harnesses_declared"] is True

    readme = workspace / "README.md"
    readme.write_text(readme.read_text() + "\nRequires Google Chrome for browser acceptance.\n")

    verified = builder.inspect(
        workspace,
        implementation,
        commands=evidence.commands,
        command_results=(
            {
                "command": evidence.commands["test"],
                "exit_code": 0,
                "timed_out": False,
                "reported_error": None,
                "browser_acceptance_receipt": True,
            },
        ),
    )
    assert verified.checks["browser_acceptance_receipt_verified"] is True
    assert verified.checks["browser_runtime_prerequisites_documented"] is True
    assert verified.passed is True

    stale_after_later_command = builder.inspect(
        workspace,
        implementation,
        commands=evidence.commands,
        command_results=(
            {
                "command": evidence.commands["test"],
                "exit_code": 0,
                "timed_out": False,
                "reported_error": None,
                "browser_acceptance_receipt": True,
            },
            {
                "command": evidence.commands["build"],
                "exit_code": 0,
                "timed_out": False,
                "reported_error": None,
                "browser_acceptance_receipt": False,
            },
        ),
    )
    assert stale_after_later_command.checks[
        "browser_acceptance_receipt_verified"
    ] is False


def test_workspace_does_not_require_browser_readme_without_named_runtime(
    build_request, tmp_path
) -> None:
    builder = ReactViteWorkspaceBuilder()
    workspace = tmp_path / "app"
    implementation = {"contract_item_ids": ["MODEL-01"]}
    evidence = builder.build(workspace, build_request, implementation)
    browser_js = workspace / "tests" / "browser.acceptance.js"
    browser_js.write_text("console.log('browser-agnostic')\n")

    inspected = builder.inspect(workspace, implementation, commands=evidence.commands)

    assert inspected.checks["browser_runtime_prerequisites_documented"] is True


def test_workspace_rejects_semantic_command_failure_with_zero_exit(
    build_request, tmp_path
) -> None:
    builder = ReactViteWorkspaceBuilder()
    workspace = tmp_path / "app"
    implementation = {"contract_item_ids": ["MODEL-01"]}
    evidence = builder.build(workspace, build_request, implementation)

    inspected = builder.inspect(
        workspace,
        implementation,
        commands=evidence.commands,
        command_results=(
            {
                "exit_code": 0,
                "timed_out": False,
                "reported_error": "playwright_cli_error",
            },
        ),
    )

    assert inspected.passed is False
