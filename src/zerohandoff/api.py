from __future__ import annotations

import asyncio
import json
import threading
import uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from zerohandoff.models import BuildRequest, RunStatus, Stage
from zerohandoff.service import AdapterName, RunService


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    build_request: BuildRequest
    adapter: AdapterName = "codex"
    frozen_snapshot: str | None = None
    run_id: str | None = None
    fault_stage: Stage | None = None


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: AdapterName | None = None
    frozen_snapshot: str | None = None


def create_app(repo_root: Path | None = None) -> FastAPI:
    root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    service = RunService(root)
    app = FastAPI(title="ZeroHandoff Control Room API", version="0.1.0")
    jobs: dict[str, threading.Thread] = {}
    jobs_lock = threading.Lock()

    def start_job(
        run_id: str,
        build_request: BuildRequest,
        adapter: AdapterName,
        frozen_snapshot: str | None,
        fault_stage: Stage | None = None,
        resume: bool = False,
    ) -> None:
        with jobs_lock:
            active = jobs.get(run_id)
            if active and active.is_alive():
                raise ValueError("run is already active")

            def worker() -> None:
                try:
                    service.deliver(
                        request=build_request,
                        adapter=adapter,
                        frozen_path=Path(frozen_snapshot) if frozen_snapshot else None,
                        run_id=run_id,
                        fault_stage=fault_stage,
                        resume=resume,
                    )
                except Exception:
                    # The orchestrator commits structured failure/cancellation evidence.
                    pass

            thread = threading.Thread(target=worker, name=f"zerohandoff-{run_id}", daemon=True)
            jobs[run_id] = thread
            thread.start()

    @app.get("/api/doctor")
    def get_doctor() -> dict:
        return service.readiness()

    @app.post("/api/runs", status_code=202)
    def create_run(payload: RunCreate) -> dict:
        run_id = payload.run_id or f"delivery_{uuid.uuid4().hex[:12]}"
        try:
            service.load_frozen(Path(payload.frozen_snapshot) if payload.frozen_snapshot else None)
            start_job(
                run_id,
                payload.build_request,
                payload.adapter,
                payload.frozen_snapshot,
                payload.fault_stage,
            )
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"run_id": run_id, "status": "accepted"}

    @app.get("/api/runs")
    def list_runs() -> list[dict]:
        return service.list_runs()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        try:
            return service.run_summary(run_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/events")
    async def stream_events(run_id: str, request: Request) -> StreamingResponse:
        try:
            service.run_summary(run_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        async def generate():
            cursor = 0
            while not await request.is_disconnected():
                events = service.events(run_id)
                for event in events[cursor:]:
                    yield f"id: {event['sequence']}\nevent: zerohandoff\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
                cursor = len(events)
                summary = service.run_summary(run_id)
                if summary["status"] in {
                    RunStatus.COMPLETED.value,
                    RunStatus.FAILED.value,
                    RunStatus.CANCELLED.value,
                    RunStatus.INFRASTRUCTURE_FAILED.value,
                }:
                    yield f"event: terminal\ndata: {json.dumps(summary, separators=(',', ':'))}\n\n"
                    break
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.get("/api/runs/{run_id}/artifacts")
    def get_artifacts(run_id: str) -> list[dict]:
        try:
            service.run_summary(run_id)
            return service.artifacts(run_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/evidence")
    def get_evidence(run_id: str) -> dict[str, list[dict]]:
        try:
            return service.evidence(run_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/bundle")
    def get_bundle(run_id: str) -> FileResponse:
        try:
            archive = service.bundle_archive(run_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(archive, filename=f"{run_id}-delivery-bundle.zip")

    @app.get("/api/runs/{run_id}/preview")
    def get_preview(run_id: str) -> FileResponse:
        try:
            summary = service.run_summary(run_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not summary["preview_ready"]:
            raise HTTPException(status_code=404, detail="application preview is not ready")
        return FileResponse(service.delivery_root / run_id / "workspace" / "app" / "dist" / "index.html")

    @app.get("/api/runs/{run_id}/video")
    def get_video(run_id: str) -> FileResponse:
        try:
            summary = service.run_summary(run_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not summary["video_ready"]:
            raise HTTPException(status_code=404, detail="demo video is not ready")
        return FileResponse(service.delivery_root / run_id / "demo" / "demo.mp4", media_type="video/mp4")

    @app.post("/api/runs/{run_id}/resume", status_code=202)
    def resume_run(run_id: str, payload: ResumeRequest) -> dict:
        try:
            root_path = service.delivery_root / run_id
            build_request = BuildRequest.model_validate_json((root_path / "build_request.json").read_text())
            manifest = json.loads((root_path / "manifest.json").read_text())
            adapter = payload.adapter or manifest["adapter"]
            frozen_snapshot = payload.frozen_snapshot or str(
                root_path / "frozen_relationship_vectors.json"
            )
            start_job(
                run_id,
                build_request,
                adapter,
                frozen_snapshot,
                resume=True,
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"run_id": run_id, "status": "resuming"}

    @app.post("/api/runs/{run_id}/cancel", status_code=202)
    def cancel_run(run_id: str) -> dict:
        try:
            service.run_summary(run_id)
            return service.cancel(run_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    ui_dist = root / "ui" / "dist"
    if (ui_dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=ui_dist / "assets"), name="assets")

    @app.get("/")
    def control_room() -> FileResponse:
        index = ui_dist / "index.html"
        if not index.exists():
            raise HTTPException(status_code=503, detail="Control Room has not been built")
        return FileResponse(index)

    return app


app = create_app()
