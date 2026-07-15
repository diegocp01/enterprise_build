from __future__ import annotations

import json

import pytest

from zerohandoff.models import ArtifactEnvelope, GateDecision, Stage
from zerohandoff.storage import RunStore, StoreError


def test_store_is_append_only_atomic_and_detects_artifact_tampering(tmp_path) -> None:
    store = RunStore(tmp_path, "delivery_test", "delivery")
    store.append_log("gates", {"stage": "SENSE", "decision": "PASS"})
    row = store.read_jsonl(store.logs_dir / "gates.jsonl")[0]
    assert row["timestamp"]

    artifact = store.commit_artifact(
        stage=Stage.SENSE,
        artifact_id="opportunity_model",
        artifact_type="opportunity_model",
        version=1,
        producer_pair="SENSE",
        lead="Mira",
        peer="Zephyr",
        files={"opportunity_model.json": {"intent": "Fixture"}},
        gate_status=GateDecision.PASS,
    )
    assert store.verify_artifact(artifact)
    legacy = artifact.model_dump(mode="json")
    legacy["requirement_ids"] = legacy.pop("contract_item_ids")
    assert ArtifactEnvelope.model_validate(legacy).contract_item_ids == []
    (store.root / artifact.content_files[0]).write_text(json.dumps({"intent": "tampered"}))
    assert not store.verify_artifact(artifact)
    with pytest.raises(StoreError, match="immutable"):
        store.commit_artifact(
            stage=Stage.SENSE,
            artifact_id="opportunity_model",
            artifact_type="opportunity_model",
            version=1,
            producer_pair="SENSE",
            lead="Mira",
            peer="Zephyr",
            files={"opportunity_model.json": {}},
        )


def test_store_rejects_path_escape(tmp_path) -> None:
    store = RunStore(tmp_path, "delivery_safe", "delivery")
    with pytest.raises(StoreError, match="escapes"):
        store.atomic_json("../escape.json", {})
