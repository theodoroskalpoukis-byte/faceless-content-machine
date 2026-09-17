from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


class ValidationError(RuntimeError):
    pass


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def validate_video(
    video: Path,
    subtitles: Path,
    *,
    min_duration: float = 25.0,
    max_duration: float = 45.0,
) -> dict[str, Any]:
    if not video.exists() or video.stat().st_size <= 100_000:
        raise ValidationError("Final MP4 is missing or implausibly small")
    if video.stat().st_size > 500 * 1024 * 1024:
        raise ValidationError("Final MP4 exceeds the 500 MB safety limit")
    probe = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate",
            "-of",
            "json",
            str(video),
        ]
    )
    data = json.loads(probe.stdout)
    duration = float(data["format"]["duration"])
    streams = data.get("streams", [])
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    failures: list[str] = []
    if not min_duration <= duration <= max_duration:
        failures.append(f"duration {duration:.2f}s is outside {min_duration}-{max_duration}s")
    if not video_stream:
        failures.append("video stream missing")
    else:
        if (int(video_stream.get("width", 0)), int(video_stream.get("height", 0))) != (1080, 1920):
            failures.append("resolution is not 1080x1920")
        if video_stream.get("codec_name") != "h264":
            failures.append("video codec is not H.264")
    if not audio_stream:
        failures.append("audio stream missing")
    elif audio_stream.get("codec_name") != "aac":
        failures.append("audio codec is not AAC")
    subtitle_text = subtitles.read_text(encoding="utf-8") if subtitles.exists() else ""
    cue_count = len(re.findall(r"-->\s*\d{2}:\d{2}:\d{2},\d{3}", subtitle_text))
    if cue_count < 5:
        failures.append("subtitle file has fewer than 5 timed cues")
    caption_proof = video.parent / "caption-proof.png"
    if not caption_proof.exists() or caption_proof.stat().st_size < 5_000:
        failures.append("burned-caption proof is missing or blank")
    else:
        caption_signal = _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-i",
                str(caption_proof),
                "-vf",
                "signalstats,metadata=print:file=-",
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ]
        )
        ymax_match = re.search(r"lavfi\.signalstats\.YMAX=([0-9.]+)", caption_signal.stdout)
        if not ymax_match or float(ymax_match.group(1)) < 200:
            failures.append("burned captions are not visibly inside the frame")

    volume = _run(["ffmpeg", "-hide_banner", "-i", str(video), "-af", "volumedetect", "-f", "null", "-"])
    volume_text = volume.stderr
    match = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", volume_text)
    mean_volume = float(match.group(1)) if match else -99.0
    if mean_volume < -35:
        failures.append(f"narration is too quiet ({mean_volume} dB)")

    black = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(video),
            "-vf",
            "blackdetect=d=1.5:pix_th=0.05",
            "-an",
            "-f",
            "null",
            "-",
        ]
    )
    black_spans = re.findall(r"black_duration:([0-9.]+)", black.stderr)
    if any(float(span) >= 1.5 for span in black_spans):
        failures.append("video contains a black span of at least 1.5 seconds")
    if failures:
        raise ValidationError("; ".join(failures))
    return {
        "valid": True,
        "duration_seconds": round(duration, 3),
        "width": int(video_stream["width"]),
        "height": int(video_stream["height"]),
        "video_codec": video_stream["codec_name"],
        "audio_codec": audio_stream["codec_name"],
        "file_size_bytes": video.stat().st_size,
        "subtitle_cues": cue_count,
        "mean_volume_db": mean_volume,
        "black_spans": black_spans,
    }
