from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote

from PIL import Image, ImageDraw, ImageStat

from zerohandoff.config import digest_file
from zerohandoff.storage import RunStore


def is_neural_narration(provider: str) -> bool:
    """Return true only for providers with explicitly declared neural provenance."""

    return provider.startswith(("edge-neural:", "supplied-neural:"))


def is_presenter_quality_narration(provider: str) -> bool:
    """Accept neural audio or an installed, enhanced, fully local macOS voice."""

    return is_neural_narration(provider) or provider.startswith("macos-enhanced:")


@dataclass(frozen=True)
class DemoEvidence:
    video_path: Path
    narration_path: Path
    screenshot_path: Path
    duration_seconds: float
    has_video: bool
    has_audio: bool
    visual_content: bool
    has_motion: bool
    capture_mode: str
    narration_provider: str
    checksum: str
    capture_report_path: Path | None = None
    mutating_actions_completed: int = 0
    unique_state_count: int = 0


class DemoAssembler:
    def assemble(
        self,
        *,
        store: RunStore,
        preview_html: Path,
        demo_plan: list[dict[str, Any]],
        narration_script: str,
        max_seconds: int,
        require_interactive: bool = False,
    ) -> DemoEvidence:
        demo_dir = store.root / "demo"
        demo_dir.mkdir(parents=True, exist_ok=True)
        plan_path = demo_dir / "demo_plan.json"
        narration_text = demo_dir / "narration.txt"
        screenshot = demo_dir / "app-preview.png"
        browser_video = demo_dir / "browser-capture.webm"
        capture_report = demo_dir / "capture_report.json"
        audio = demo_dir / "narration.mp3"
        video = demo_dir / "demo.mp4"
        plan_path.write_text(json.dumps(demo_plan, indent=2, sort_keys=True) + "\n")
        narration_text.write_text(narration_script.strip() + "\n")
        audio_source, narration_provider = self._narrate(
            narration_script,
            audio,
            prefer_presenter_quality=require_interactive,
        )
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            raise RuntimeError("ffmpeg and ffprobe are required for demo assembly")
        narration_duration = (
            self._media_duration(ffprobe, audio_source) if audio_source else 0.0
        )
        if require_interactive and not is_presenter_quality_narration(
            narration_provider
        ):
            raise RuntimeError(
                "live Codex demos require presenter-quality narration; use the installed "
                "enhanced macOS voice, explicitly opt in to Edge TTS, or supply audio "
                "with declared provenance"
            )
        if (
            require_interactive
            and narration_provider.startswith("macos-enhanced:")
            and os.getenv("ZEROHANDOFF_NARRATION_AUDIO") is None
        ):
            local_rate = int(os.getenv("ZEROHANDOFF_SAY_RATE", "165"))
            for _ in range(2):
                try:
                    self._capture_pacing_scale(demo_plan, narration_duration)
                    break
                except RuntimeError:
                    pass
                adaptive_rate = self._adaptive_say_rate(
                    demo_plan,
                    narration_duration,
                    base_rate=local_rate,
                )
                local_rate = adaptive_rate
                audio_source, narration_provider = self._narrate(
                    narration_script,
                    audio,
                    prefer_presenter_quality=True,
                    local_rate=adaptive_rate,
                )
                narration_duration = (
                    self._media_duration(ffprobe, audio_source)
                    if audio_source
                    else 0.0
                )
        pacing_scale = (
            self._capture_pacing_scale(demo_plan, narration_duration)
            if require_interactive
            else 1.0
        )
        capture_mode = self._capture(
            preview_html,
            browser_video,
            screenshot,
            plan_path,
            capture_report,
            pacing_scale=pacing_scale,
        )
        capture_data = (
            json.loads(capture_report.read_text())
            if capture_report.is_file()
            else {}
        )
        if require_interactive and capture_mode != "browser-interactive":
            reason = capture_data.get("error") or "interactive browser capture unavailable"
            raise RuntimeError(f"live demo capture failed: {reason}")
        visual_content = self._has_visual_content(screenshot)
        if not visual_content:
            raise RuntimeError("demo screenshot visual-content gate failed")
        capture_duration = 0.0
        synchronization_ratio = 1.0
        if browser_video.exists() and audio_source:
            capture_duration = self._media_duration(ffprobe, browser_video)
            synchronization_ratio = self._synchronization_ratio(
                capture_duration, narration_duration
            )
            target_duration = min(narration_duration + 0.25, max_seconds - 0.1)
            command = [
                ffmpeg,
                "-y",
                "-i",
                str(browser_video),
                "-i",
                str(audio_source),
                "-filter_complex",
                (
                    "[0:v]scale=1280:720:force_original_aspect_ratio=decrease,"
                    "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                    f"setpts={synchronization_ratio:.8f}*PTS,"
                    f"tpad=stop_mode=clone:stop_duration={target_duration:.3f},"
                    "format=yuv420p[v];"
                    f"[1:a]apad=pad_dur={target_duration:.3f},"
                    "loudnorm=I=-16:TP=-1.5:LRA=11[a]"
                ),
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-t",
                f"{target_duration:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
                str(video),
            ]
        else:
            # Deterministic CI fallback. Production/demo runs are expected to use the
            # interactive browser capture above; this at least avoids a perfectly
            # static frame when a browser runtime is unavailable.
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
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-t",
                "8",
                "-vf",
                (
                    "scale=1360:765,zoompan="
                    "z='min(zoom+0.0008,1.08)':d=240:s=1280x720:fps=30,"
                    "format=yuv420p"
                ),
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
        has_motion = self._has_motion(ffmpeg, video)
        if duration > max_seconds or not has_video or not has_audio or not has_motion:
            raise RuntimeError("demo media gate failed")
        evidence = DemoEvidence(
            video_path=video,
            narration_path=narration_text,
            screenshot_path=screenshot,
            duration_seconds=duration,
            has_video=has_video,
            has_audio=has_audio,
            visual_content=visual_content,
            has_motion=has_motion,
            capture_mode=capture_mode,
            narration_provider=narration_provider,
            checksum=digest_file(video),
            capture_report_path=capture_report if capture_report.is_file() else None,
            mutating_actions_completed=int(
                capture_data.get("mutating_actions_completed", 0)
            ),
            unique_state_count=int(capture_data.get("unique_state_count", 0)),
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
                "visual_content": visual_content,
                "has_motion": has_motion,
                "capture_mode": capture_mode,
                "narration_provider": narration_provider,
                "capture_duration_seconds": capture_duration,
                "narration_duration_seconds": narration_duration,
                "synchronization_ratio": synchronization_ratio,
                "capture_report": (
                    str(capture_report.relative_to(store.root))
                    if capture_report.is_file()
                    else None
                ),
                "mutating_actions_completed": evidence.mutating_actions_completed,
                "unique_state_count": evidence.unique_state_count,
                "checksum": evidence.checksum,
            },
        )
        return evidence

    @staticmethod
    def _capture(
        preview_html: Path,
        video: Path,
        screenshot: Path,
        plan_path: Path,
        report_path: Path,
        *,
        pacing_scale: float = 1.0,
    ) -> str:
        for generated in (video, screenshot, report_path):
            generated.unlink(missing_ok=True)
        chrome_candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path(shutil.which("google-chrome") or "/nonexistent"),
            Path(shutil.which("chromium") or "/nonexistent"),
        ]
        chrome = next((path for path in chrome_candidates if path.exists()), None)
        runtime = DemoAssembler._playwright_runtime()
        recorder = Path(__file__).resolve().parents[3] / "scripts" / "record_browser_demo.mjs"
        if chrome and runtime and recorder.is_file():
            node, node_modules = runtime
            class QuietHandler(SimpleHTTPRequestHandler):
                def log_message(self, *_args: Any) -> None:
                    return

            handler = partial(QuietHandler, directory=str(preview_html.resolve().parent))
            try:
                server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            except OSError:
                server = None
            if server is not None:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    url = (
                        f"http://127.0.0.1:{server.server_port}/"
                        f"{quote(preview_html.name)}"
                    )
                    completed = subprocess.run(
                        [
                            str(node),
                            str(recorder),
                            "--url",
                            url,
                            "--output",
                            str(video),
                            "--screenshot",
                            str(screenshot),
                            "--plan",
                            str(plan_path),
                            "--report",
                            str(report_path),
                            "--node-modules",
                            str(node_modules),
                            "--chrome",
                            str(chrome),
                            "--pacing-scale",
                            f"{pacing_scale:.8f}",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=180,
                        check=False,
                        env=DemoAssembler._playwright_environment(
                            node_modules
                        ),
                    )
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)
                if (
                    completed.returncode == 0
                    and video.exists()
                    and video.stat().st_size > 10_000
                    and screenshot.exists()
                    and DemoAssembler._has_visual_content(screenshot)
                ):
                    try:
                        report = json.loads(report_path.read_text())
                    except (OSError, json.JSONDecodeError):
                        report = {}
                    if (
                        report.get("completed") is True
                        and int(report.get("mutating_actions_completed", 0)) >= 2
                        and int(report.get("unique_state_count", 0)) >= 2
                        and not report.get("error")
                    ):
                        return "browser-interactive"
                if not report_path.exists():
                    report_path.write_text(
                        json.dumps(
                            {
                                "schema_version": "1.0",
                                "completed": False,
                                "error": (
                                    completed.stderr[-2000:]
                                    or f"recorder exited {completed.returncode}"
                                ),
                                "mutating_actions_completed": 0,
                                "unique_state_count": 0,
                            },
                            indent=2,
                            sort_keys=True,
                        )
                        + "\n"
                    )
        # Browser-free test fallback.
        if not report_path.exists():
            report_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "completed": False,
                        "error": "Playwright, Chrome, or the recorder was unavailable",
                        "mutating_actions_completed": 0,
                        "unique_state_count": 0,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
        image = Image.new("RGB", (1280, 720), "#f6f1e7")
        draw = ImageDraw.Draw(image)
        draw.rectangle((70, 70, 1210, 650), fill="#ffffff", outline="#111827", width=5)
        draw.text((120, 130), "ZEROHANDOFF DELIVERY", fill="#111827")
        draw.text((120, 200), "Interactive browser recording unavailable", fill="#334155")
        draw.text((120, 570), "Generated application preview", fill="#111827")
        image.save(screenshot)
        return "fallback"

    @staticmethod
    def _playwright_runtime() -> tuple[Path, Path] | None:
        configured = os.getenv("ZEROHANDOFF_NODE_MODULES")
        candidates = [Path(configured).expanduser()] if configured else []
        candidates.extend(
            Path.home().glob(
                ".cache/codex-runtimes/*/dependencies/node/node_modules"
            )
        )
        candidates.append(Path(__file__).resolve().parents[3] / "node_modules")
        for node_modules in candidates:
            if not (node_modules / "playwright" / "package.json").is_file():
                continue
            bundled_node = node_modules.parent / "bin" / "node"
            node = bundled_node if bundled_node.is_file() else Path(shutil.which("node") or "")
            if node.is_file():
                return node, node_modules
        return None

    @staticmethod
    def _playwright_environment(node_modules: Path) -> dict[str, str]:
        environment = os.environ.copy()
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return environment
        registry_files = list(
            node_modules.glob(
                ".pnpm/playwright-core@*/node_modules/playwright-core/browsers.json"
            )
        )
        if not registry_files:
            return environment
        registry = json.loads(registry_files[0].read_text())
        record = next(
            (
                item
                for item in registry.get("browsers", [])
                if item.get("name") == "ffmpeg"
            ),
            None,
        )
        if not record:
            return environment
        cache = Path(tempfile.gettempdir()) / "zerohandoff-playwright-browsers"
        executable_name = {
            "Darwin": "ffmpeg-mac",
            "Windows": "ffmpeg-win64.exe",
        }.get(platform.system(), "ffmpeg-linux")
        executable = cache / f"ffmpeg-{record['revision']}" / executable_name
        executable.parent.mkdir(parents=True, exist_ok=True)
        if not executable.exists():
            executable.symlink_to(ffmpeg)
        environment["PLAYWRIGHT_BROWSERS_PATH"] = str(cache)
        return environment

    @staticmethod
    def _has_visual_content(path: Path) -> bool:
        try:
            with Image.open(path) as source:
                image = source.convert("RGB")
                image.thumbnail((240, 135))
                statistics = ImageStat.Stat(image)
                colors = image.getcolors(maxcolors=240 * 135)
                color_count = len(colors) if colors is not None else 240 * 135
                return color_count >= 8 and max(statistics.stddev) >= 4.0
        except (OSError, ValueError):
            return False

    @staticmethod
    def _narrate(
        script: str,
        destination: Path,
        *,
        prefer_presenter_quality: bool = False,
        local_rate: int | None = None,
    ) -> tuple[Path | None, str]:
        destination.unlink(missing_ok=True)
        destination.with_suffix(".aiff").unlink(missing_ok=True)
        supplied = os.getenv("ZEROHANDOFF_NARRATION_AUDIO")
        if supplied:
            source = Path(supplied).expanduser()
            if source.is_file() and source.stat().st_size > 4_096:
                copied = destination.with_suffix(source.suffix)
                shutil.copy2(source, copied)
                provider = os.getenv(
                    "ZEROHANDOFF_NARRATION_PROVIDER", "supplied-audio"
                ).strip()
                return copied, provider or "supplied-audio"
        # Network TTS is opt-in. Campaign runs default to a local macOS voice so
        # demo generation never makes an undeclared external call.
        if os.getenv("ZEROHANDOFF_ENABLE_EDGE_TTS") == "1":
            try:
                import edge_tts  # type: ignore[import-not-found]

                voice = os.getenv(
                    "ZEROHANDOFF_TTS_VOICE", "en-US-AvaMultilingualNeural"
                )
                rate = os.getenv("ZEROHANDOFF_TTS_RATE", "-3%")
                asyncio.run(
                    edge_tts.Communicate(script, voice=voice, rate=rate).save(
                        str(destination)
                    )
                )
                if destination.exists() and destination.stat().st_size > 4_096:
                    return destination, f"edge-neural:{voice}"
            except Exception:
                pass
        say = shutil.which("say")
        if not say:
            return None, "none"
        aiff = destination.with_suffix(".aiff")
        default_local_voice = (
            "com.apple.voice.enhanced.en-US.Samantha"
            if prefer_presenter_quality
            else "Samantha"
        )
        completed = subprocess.run(
            [
                say,
                "-v",
                os.getenv(
                    "ZEROHANDOFF_SAY_VOICE",
                    default_local_voice,
                ),
                "-r",
                str(local_rate or int(os.getenv("ZEROHANDOFF_SAY_RATE", "165"))),
                "-o",
                str(aiff),
                script,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        # On some macOS runners ``say`` can create only an empty AIFF header.
        # Treat that as unavailable so ffmpeg uses the deterministic audio fixture.
        result = (
            aiff
            if completed.returncode == 0
            and aiff.exists()
            and aiff.stat().st_size > 4_096
            else None
        )
        if not result:
            return None, "none"
        voice = os.getenv(
            "ZEROHANDOFF_SAY_VOICE",
            default_local_voice,
        )
        enhanced_voice_ids = {
            "Samantha",
            "Samantha (English (US))",
            "com.apple.voice.enhanced.en-US.Samantha",
        }
        provider = (
            f"macos-enhanced:{voice}"
            if voice in enhanced_voice_ids
            else f"macos-say:{voice}"
        )
        return result, provider

    @staticmethod
    def _capture_pacing_scale(
        demo_plan: list[dict[str, Any]], narration_duration: float
    ) -> float:
        """Plan browser choreography before launching a two-minute recording.

        Every wait remains an executed assertion, but only the first wait in each
        step is visually showcased. The resulting scale aligns the planned visual
        holds with the measured narration while keeping cursor motion honest.
        """

        if narration_duration <= 0:
            raise RuntimeError("demo timing preflight requires narrated audio")
        choreography = DemoAssembler._capture_choreography_seconds(demo_plan)
        fixed_overhead = 8.0
        scale = (narration_duration - fixed_overhead) / choreography
        if not 0.45 <= scale <= 1.8:
            raise RuntimeError(
                "demo plan and narration fail timing preflight; revise their relative detail "
                "before browser capture"
            )
        return scale

    @staticmethod
    def _capture_choreography_seconds(demo_plan: list[dict[str, Any]]) -> float:
        steps = len(demo_plan)
        actions = [
            action
            for step in demo_plan
            for action in (step.get("actions") or step.get("browser_actions") or [])
            if isinstance(action, dict)
        ]
        interactive = sum(
            str(action.get("type", "")) in {"click", "fill", "select", "scroll"}
            for action in actions
        )
        waits = sum(str(action.get("type", "")) == "wait" for action in actions)
        showcased_waits = sum(
            any(
                str(action.get("type", "")) == "wait"
                for action in (step.get("actions") or step.get("browser_actions") or [])
                if isinstance(action, dict)
            )
            for step in demo_plan
        )
        passive_waits = max(0, waits - showcased_waits)
        choreography = (
            steps * 1.28
            + interactive * 1.37
            + showcased_waits * 0.87
            + passive_waits * 0.04
        )
        if choreography <= 0:
            raise RuntimeError("demo timing preflight found no recordable choreography")
        return choreography

    @staticmethod
    def _adaptive_say_rate(
        demo_plan: list[dict[str, Any]],
        narration_duration: float,
        *,
        base_rate: int,
    ) -> int:
        target_duration = 8.0 + 1.65 * DemoAssembler._capture_choreography_seconds(
            demo_plan
        )
        if narration_duration <= 0 or target_duration <= 0:
            raise RuntimeError("cannot adapt an invalid narration duration")
        calculated = round(base_rate * narration_duration / target_duration)
        return max(180, min(320, calculated))

    @staticmethod
    def _media_duration(ffprobe: str, path: Path) -> float:
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if completed.returncode != 0:
            return 0.0
        return float(json.loads(completed.stdout)["format"]["duration"])

    @staticmethod
    def _synchronization_ratio(
        capture_duration: float, narration_duration: float
    ) -> float:
        """Return a bounded visual time-scale so narration and workflow end together."""

        if capture_duration <= 0 or narration_duration <= 0:
            raise RuntimeError("demo capture and narration must both have duration")
        ratio = narration_duration / capture_duration
        if not 0.65 <= ratio <= 1.55:
            raise RuntimeError(
                "demo narration and browser capture are too far apart to synchronize honestly"
            )
        return ratio

    @staticmethod
    def _has_motion(ffmpeg: str, path: Path) -> bool:
        completed = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                str(path),
                "-vf",
                "fps=1,scale=160:90,format=rgb24",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
        frame_size = 160 * 90 * 3
        frames = [
            completed.stdout[offset : offset + frame_size]
            for offset in range(0, len(completed.stdout), frame_size)
            if len(completed.stdout[offset : offset + frame_size]) == frame_size
        ]
        if len(frames) < 2:
            return False
        first = frames[0]
        for frame in frames[1:]:
            mean_difference = sum(abs(left - right) for left, right in zip(first, frame)) / frame_size
            if mean_difference >= 1.0:
                return True
        return False
