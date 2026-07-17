#!/usr/bin/env python3
"""Assemble the final narrated ZeroHandoff hackathon video."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def duration(path: Path, ffprobe: str) -> float:
    completed = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(json.loads(completed.stdout)["format"]["duration"])


def narration_from_markdown(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    match = re.search(r"## Narration\s+(.*?)\s+## Visual sequence", source, re.S)
    if not match:
        raise RuntimeError("DEMO_SCRIPT.md does not contain a narration section")
    return " ".join(line.strip() for line in match.group(1).splitlines() if line.strip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overview", type=Path, required=True)
    parser.add_argument("--product-demo", type=Path, required=True)
    parser.add_argument("--script", type=Path, default=ROOT / "submission" / "DEMO_SCRIPT.md")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "submission" / "media" / "zerohandoff-submission-demo.mp4",
    )
    parser.add_argument("--voice-rate", type=int, default=240)
    args = parser.parse_args()
    args.overview = args.overview.resolve()
    args.product_demo = args.product_demo.resolve()
    args.script = args.script.resolve()
    args.output = args.output.resolve()

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    say = shutil.which("say")
    if not ffmpeg or not ffprobe or not say:
        raise RuntimeError("ffmpeg, ffprobe, and macOS say are required")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    narration_text = narration_from_markdown(args.script)
    narration_txt = args.output.with_name("zerohandoff-submission-narration.txt")
    narration_aiff = args.output.with_name("zerohandoff-submission-narration.aiff").resolve()
    narration_txt.write_text(narration_text + "\n", encoding="utf-8")
    run(
        [
            say,
            "-v",
            "com.apple.voice.enhanced.en-US.Samantha",
            "-r",
            str(args.voice_rate),
            "-o",
            str(narration_aiff),
            narration_text,
        ]
    )

    visual_duration = duration(args.overview, ffprobe) + duration(args.product_demo, ffprobe)
    narration_duration = duration(narration_aiff, ffprobe)
    target_duration = max(visual_duration, narration_duration) + 0.3
    video_padding = max(0.3, target_duration - visual_duration)
    audio_padding = max(0.3, target_duration - narration_duration)

    filter_graph = (
        "[0:v]scale=1280:720:force_original_aspect_ratio=decrease,"
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=#101820,setsar=1,fps=30[v0];"
        "[1:v]scale=1280:720:force_original_aspect_ratio=decrease,"
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=#101820,setsar=1,fps=30[v1];"
        "[v0][v1]concat=n=2:v=1:a=0,"
        f"tpad=stop_mode=clone:stop_duration={video_padding:.3f}[video];"
        f"[2:a]apad=pad_dur={audio_padding:.3f}[audio]"
    )
    run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(args.overview),
            "-i",
            str(args.product_demo),
            "-i",
            str(narration_aiff),
            "-filter_complex",
            filter_graph,
            "-map",
            "[video]",
            "-map",
            "[audio]",
            "-t",
            f"{target_duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "19",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(args.output),
        ]
    )
    narration_aiff.unlink(missing_ok=True)
    print(
        json.dumps(
            {
                "output": str(args.output.relative_to(ROOT)),
                "duration_seconds": round(duration(args.output, ffprobe), 3),
                "visual_source_seconds": round(visual_duration, 3),
                "narration_seconds": round(narration_duration, 3),
                "voice": "macOS enhanced Samantha",
                "voice_rate": args.voice_rate,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
