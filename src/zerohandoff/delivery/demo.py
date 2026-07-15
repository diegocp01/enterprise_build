from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from zerohandoff.config import digest_file
from zerohandoff.storage import RunStore


@dataclass(frozen=True)
class DemoEvidence:
    video_path: Path
    narration_path: Path
    screenshot_path: Path
    duration_seconds: float
    has_video: bool
    has_audio: bool
    checksum: str


class DemoAssembler:
    def assemble(
        self,
        *,
        store: RunStore,
        preview_html: Path,
        demo_plan: list[dict[str, Any]],
        narration_script: str,
        max_seconds: int,
    ) -> DemoEvidence:
        demo_dir = store.root / "demo"
        demo_dir.mkdir(parents=True, exist_ok=True)
        plan_path = demo_dir / "demo_plan.json"
        narration_text = demo_dir / "narration.txt"
        screenshot = demo_dir / "app-preview.png"
        audio = demo_dir / "narration.aiff"
        video = demo_dir / "demo.mp4"
        plan_path.write_text(json.dumps(demo_plan, indent=2, sort_keys=True) + "\n")
        narration_text.write_text(narration_script.strip() + "\n")
        self._capture(preview_html, screenshot, narration_script)
        audio_source = self._narrate(narration_script, audio)
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            raise RuntimeError("ffmpeg and ffprobe are required for demo assembly")
        if audio_source:
            command = [
                ffmpeg,
                "-y",
                "-loop",
                "1",
                "-i",
                str(screenshot),
                "-i",
                str(audio_source),
                "-shortest",
                "-t",
                "8",
                "-vf",
                "scale=1280:720,format=yuv420p",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                str(video),
            ]
        else:
            command = [
                ffmpeg,
                "-y",
                "-loop",
                "1",
                "-i",
                str(screenshot),
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=3",
                "-shortest",
                "-vf",
                "scale=1280:720,format=yuv420p",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                str(video),
            ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        store.append_log(
            "commands",
            {
                "stage": "DEMO",
                "command": command,
                "exit_code": completed.returncode,
                "stdout_tail": completed.stdout[-2000:],
                "stderr_tail": completed.stderr[-2000:],
            },
        )
        if completed.returncode != 0 or not video.exists() or video.stat().st_size < 1_000:
            raise RuntimeError(f"ffmpeg demo composition failed: {completed.stderr[-1000:]}")
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-show_streams", "-of", "json", str(video)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if probe.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {probe.stderr[-1000:]}")
        metadata = json.loads(probe.stdout)
        duration = float(metadata["format"]["duration"])
        has_video = any(stream.get("codec_type") == "video" for stream in metadata["streams"])
        has_audio = any(stream.get("codec_type") == "audio" for stream in metadata["streams"])
        if duration > max_seconds or not has_video or not has_audio:
            raise RuntimeError("demo media gate failed")
        evidence = DemoEvidence(
            video_path=video,
            narration_path=narration_text,
            screenshot_path=screenshot,
            duration_seconds=duration,
            has_video=has_video,
            has_audio=has_audio,
            checksum=digest_file(video),
        )
        store.append_log(
            "demo",
            {
                "stage": "DEMO",
                "status": "completed",
                "plan": str(plan_path.relative_to(store.root)),
                "video": str(video.relative_to(store.root)),
                "duration_seconds": duration,
                "has_video": has_video,
                "has_audio": has_audio,
                "checksum": evidence.checksum,
            },
        )
        return evidence

    @staticmethod
    def _capture(preview_html: Path, destination: Path, caption: str) -> None:
        chrome_candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path(shutil.which("google-chrome") or "/nonexistent"),
            Path(shutil.which("chromium") or "/nonexistent"),
        ]
        chrome = next((path for path in chrome_candidates if path.exists()), None)
        if chrome:
            completed = subprocess.run(
                [
                    str(chrome),
                    "--headless=new",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--window-size=1280,720",
                    f"--screenshot={destination}",
                    preview_html.resolve().as_uri(),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if completed.returncode == 0 and destination.exists():
                return
        image = Image.new("RGB", (1280, 720), "#f6f1e7")
        draw = ImageDraw.Draw(image)
        draw.rectangle((70, 70, 1210, 650), fill="#ffffff", outline="#111827", width=5)
        draw.text((120, 130), "ZEROHANDOFF DELIVERY", fill="#111827")
        draw.text((120, 200), caption[:140], fill="#334155")
        draw.text((120, 570), "Generated application preview", fill="#111827")
        image.save(destination)

    @staticmethod
    def _narrate(script: str, destination: Path) -> Path | None:
        say = shutil.which("say")
        if not say:
            return None
        completed = subprocess.run(
            [say, "-o", str(destination), script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        # On some macOS runners ``say`` can create only an empty AIFF header.
        # Treat that as unavailable so ffmpeg uses the deterministic audio fixture.
        return (
            destination
            if completed.returncode == 0
            and destination.exists()
            and destination.stat().st_size > 4_096
            else None
        )
