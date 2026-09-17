from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .models import FootageClip
from .tts import NarrationResult


class RenderError(RuntimeError):
    pass


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def media_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


class VideoRenderer:
    def __init__(self, width: int = 1080, height: int = 1920, fps: int = 30):
        self.width = width
        self.height = height
        self.fps = fps

    def render(
        self,
        clips: list[FootageClip],
        narration: NarrationResult,
        output_dir: Path,
    ) -> Path:
        if len(clips) < 4:
            raise RenderError("At least four clips are required")
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        total = media_duration(narration.audio_path)
        segment_duration = total / len(clips)
        segments: list[Path] = []
        for index, clip in enumerate(clips):
            segment = output_dir / f"segment-{index:02d}.mp4"
            video_filter = (
                f"scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
                f"crop={self.width}:{self.height},fps={self.fps},"
                "eq=contrast=1.03:saturation=0.92"
            )
            run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(Path(clip.file_path).resolve()),
                    "-t",
                    f"{segment_duration:.3f}",
                    "-vf",
                    video_filter,
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "24",
                    "-pix_fmt",
                    "yuv420p",
                    str(segment),
                ]
            )
            segments.append(segment)
        concat_file = output_dir / "segments.txt"
        concat_file.write_text(
            "\n".join(f"file '{segment.resolve().as_posix()}'" for segment in segments) + "\n",
            encoding="utf-8",
        )
        output = output_dir / "short.mp4"
        subtitle_path = narration.subtitles_path.resolve().as_posix().replace("'", "\\'").replace(":", "\\:")
        style = (
            "FontName=DejaVu Sans,FontSize=17,Bold=1,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00151515,BorderStyle=1,Outline=2,Shadow=1,"
            "Alignment=2,MarginL=18,MarginR=18,MarginV=50"
        )
        caption_filter = f"subtitles='{subtitle_path}':force_style='{style}'"
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-i",
                str(narration.audio_path.resolve()),
                "-vf",
                caption_filter,
                "-af",
                "loudnorm=I=-16:LRA=7:TP=-1.5",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "22",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-ar",
                "48000",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output),
            ]
        )
        if not output.exists() or output.stat().st_size == 0:
            raise RenderError("FFmpeg did not create the final MP4")
        caption_proof = output_dir / "caption-proof.png"
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=black:s={self.width}x{self.height}:r=1:d=1",
                "-vf",
                caption_filter,
                "-frames:v",
                "1",
                "-update",
                "1",
                str(caption_proof),
            ]
        )
        return output
