from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import uvicorn

from zerohandoff.api import create_app
from zerohandoff.models import BuildRequest, Stage
from zerohandoff.schemas import export_schemas
from zerohandoff.service import RunService


def _json(value: Any) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zerohandoff-internal",
        description="Internal deterministic engine invoked by ZeroHandoff Codex skills.",
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--live", action="store_true")
    train = commands.add_parser("train")
    train.add_argument("--adapter", choices=("fixture", "codex"), default="fixture")
    train.add_argument("--rounds", type=int, default=None)
    train.add_argument("--run-id")
    run = commands.add_parser("run")
    run.add_argument("--request", required=True, type=Path)
    run.add_argument("--adapter", choices=("fixture", "codex"), default="fixture")
    run.add_argument("--frozen", type=Path)
    run.add_argument("--run-id")
    run.add_argument("--resume", action="store_true")
    run.add_argument("--fault-stage", choices=[stage.value for stage in Stage])
    repair_learning = commands.add_parser("repair-learning")
    repair_learning.add_argument("--run-id", required=True)
    repair_learning.add_argument("--reason", required=True)
    repair_demo = commands.add_parser("repair-demo")
    repair_demo.add_argument("--run-id", required=True)
    status = commands.add_parser("status")
    status.add_argument("run_id", nargs="?")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    schemas = commands.add_parser("schemas")
    schemas.add_argument("--output", type=Path, default=Path("schemas"))
    puzzles = commands.add_parser("puzzles")
    puzzles.add_argument("action", choices=("generate", "audit", "validate"))
    puzzles.add_argument("--corpus", type=Path, default=Path("data/puzzles.jsonl"))
    puzzles.add_argument("--stats", type=Path, default=Path("data/puzzle_stats.json"))
    puzzles.add_argument("--seed", type=int, default=42)
    return parser


def main() -> int:
    args = _parser().parse_args()
    service = RunService(args.repo)
    if args.command == "doctor":
        from zerohandoff.doctor import doctor

        report = doctor(args.repo, live=args.live)
        _json(report)
        return 0 if report["ok"] else 1
    if args.command == "train":
        result = service.train(adapter=args.adapter, rounds=args.rounds, run_id=args.run_id)
        _json(
            {
                "run_id": result.run_id,
                "status": result.status,
                "store_root": result.store_root,
                "metrics": result.metrics,
                "frozen_digest": result.frozen_snapshot.content_digest
                if result.frozen_snapshot
                else None,
            }
        )
        return 0
    if args.command == "run":
        request = BuildRequest.model_validate_json(args.request.read_text())
        result = service.deliver(
            request=request,
            adapter=args.adapter,
            frozen_path=args.frozen,
            run_id=args.run_id,
            fault_stage=Stage(args.fault_stage) if args.fault_stage else None,
            resume=args.resume,
        )
        _json(
            {
                "run_id": result.run_id,
                "status": result.status,
                "store_root": result.store_root,
                "bundle": result.bundle.bundle_dir,
                "video": result.demo.video_path,
            }
        )
        return 0
    if args.command == "repair-learning":
        _json(service.invalidate_learning_commit(args.run_id, reason=args.reason))
        return 0
    if args.command == "repair-demo":
        _json(service.repair_demo(args.run_id))
        return 0
    if args.command == "status":
        _json(service.run_summary(args.run_id) if args.run_id else service.list_runs())
        return 0
    if args.command == "schemas":
        _json({"schemas": [str(path) for path in export_schemas(args.output)]})
        return 0
    if args.command == "puzzles":
        from zerohandoff.training.corpus import (
            audit_corpus,
            generate_corpus,
            validate_corpus,
            write_corpus,
        )
        from zerohandoff.training.puzzles import PuzzleRepository

        corpus_path = (args.repo / args.corpus).resolve()
        stats_path = (args.repo / args.stats).resolve()
        if args.action == "generate":
            report = write_corpus(
                generate_corpus(args.seed),
                corpus_path,
                stats_path,
                seed=args.seed,
            )
        else:
            repository = PuzzleRepository.load(corpus_path)
            report = (
                audit_corpus(repository.puzzles)
                if args.action == "audit"
                else validate_corpus(repository.puzzles, seed=args.seed)
            )
        _json(report)
        return 0 if report["ok"] else 1
    if args.command == "serve":
        uvicorn.run(create_app(args.repo), host=args.host, port=args.port)
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
