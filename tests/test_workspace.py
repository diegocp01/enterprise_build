from __future__ import annotations

from zerohandoff.delivery.workspace import ReactViteWorkspaceBuilder


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
