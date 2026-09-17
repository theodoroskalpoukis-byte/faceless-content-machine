from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .content import ContentEngine, subtitle_chunks
from .footage import PexelsClient
from .gemini import GeminiClient
from .history import ContentHistory
from .models import ContentPackage, FootageClip, PublishResult, Source
from .publishers import TikTokPublisher, YouTubePublisher
from .render import VideoRenderer
from .research import TopicResearcher
from .tts import NarrationResult, PiperNarrator
from .validation import validate_video


class PipelineError(RuntimeError):
    pass


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _fixture_content() -> ContentPackage:
    script = (
        "A medieval king once held a feast above a frozen river. In 1410, King Henry the Fourth "
        "of England hosted guests inside a temporary hall built on the frozen River Thames. The ice "
        "was strong enough for fires, food stalls, games, and crowds. These gatherings became known "
        "as frost fairs. They were possible during unusually cold winters, when the old London Bridge "
        "slowed the river and helped thick ice form. The frozen city briefly turned its river into a street."
    )
    return ContentPackage(
        topic="Fixture: a frost fair on the River Thames",
        hook="A medieval king once held a feast above a frozen river.",
        script=script,
        historical_period="1410",
        main_person_or_event="River Thames frost fair",
        sources=[
            Source("https://www.museumoflondon.org.uk/", "Museum of London fixture", ["Test only"]),
            Source("https://www.britannica.com/", "Britannica fixture", ["Test only"]),
        ],
        visual_queries=["frozen river", "medieval feast", "old London bridge", "winter market"],
        youtube_title="The Frozen River That Became a Street #Shorts",
        youtube_description="Deterministic rendering fixture. This output is never publishable.",
        tiktok_caption="A frozen river once became a London street. Test fixture only.",
        hashtags=["history", "London", "Shorts"],
        subtitle_chunks=subtitle_chunks(script),
        verification={"verified": True, "fixture": True},
    )


def _fixture_assets(root: Path, content: ContentPackage) -> tuple[list[FootageClip], NarrationResult]:
    asset_dir = root / "fixture-assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    colors = ["#284B63", "#3C6E71", "#5A4A42", "#8D6A5A", "#355070", "#6D597A"]
    clips: list[FootageClip] = []
    for index, color in enumerate(colors):
        path = asset_dir / f"fixture-{index}.mp4"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"color=c={color}:s=540x960:r=30:d=7",
                "-vf", "noise=alls=10:allf=t+u,format=yuv420p",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", str(path),
            ],
            check=True,
        )
        clips.append(FootageClip(-(index + 1), f"fixture-{index}", "fixture", "fixture", str(path)))
    narration_dir = root / "fixture-narration"
    narration_dir.mkdir(parents=True, exist_ok=True)
    audio = narration_dir / "narration.wav"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=48000:duration=30", "-filter:a", "volume=0.12", str(audio),
        ],
        check=True,
    )
    srt = narration_dir / "captions.srt"
    chunks = content.subtitle_chunks
    cue = 30.0 / len(chunks)
    lines: list[str] = []
    from .tts import srt_time

    for index, chunk in enumerate(chunks, start=1):
        lines.append(
            f"{index}\n{srt_time((index - 1) * cue)} --> {srt_time(index * cue - 0.04)}\n{chunk}\n"
        )
    srt.write_text("\n".join(lines), encoding="utf-8")
    return clips, NarrationResult(audio, srt, 30.0, len(chunks))


def _history_item(
    production_date: str,
    content: ContentPackage | None,
    *,
    status: str,
    validation: dict[str, Any] | None = None,
    footage: list[FootageClip] | None = None,
    publications: list[PublishResult] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "date": production_date,
        "status": status,
        "errors": [error] if error else [],
        "validation": validation or {},
        "footage": [asdict(clip) for clip in footage or []],
        "publishing": [result.to_dict() for result in publications or []],
    }
    if content:
        base.update(content.to_dict())
    return base


def run_pipeline(*, fixture: bool = False, production_date: date | None = None) -> dict[str, Any]:
    settings = Settings.from_env()
    production_date = production_date or date.today()
    date_key = production_date.isoformat()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    work = settings.output_dir / date_key
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    history = ContentHistory(settings.history_path)
    content: ContentPackage | None = None
    clips: list[FootageClip] = []
    publications: list[PublishResult] = []
    try:
        if fixture:
            if settings.publish_mode != "dry-run" or settings.enable_youtube or settings.enable_tiktok:
                raise PipelineError("Fixture runs are permanently locked to dry-run mode")
            content = _fixture_content()
            clips, narration = _fixture_assets(work, content)
        else:
            prior = history.find_date(date_key)
            if prior and prior.get("status") == "published":
                raise PipelineError(f"{date_key} is already marked published; refusing a duplicate run")
            research = TopicResearcher(settings.seeds_path, history).select(production_date)
            content = ContentEngine(GeminiClient(), history).create(research)
            narration = PiperNarrator().synthesize(content.subtitle_chunks, work / "narration")
            clips = PexelsClient().acquire(
                content.visual_queries,
                work / "footage",
                target_count=6,
                seed=f"{date_key}:{content.topic}",
            )
        video = VideoRenderer(settings.width, settings.height, settings.fps).render(
            clips, narration, work / "render"
        )
        validation = validate_video(
            video,
            narration.subtitles_path,
            min_duration=settings.min_duration,
            max_duration=settings.max_duration,
        )
        metadata = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "fixture": fixture,
            "content": content.to_dict(),
            "footage": [asdict(clip) for clip in clips],
            "validation": validation,
            "video": str(video),
        }
        atomic_json(work / "metadata.json", metadata)
        if settings.publish_mode != "dry-run":
            privacy = "private" if settings.publish_mode == "private" else "public"
            if not settings.enable_youtube and not settings.enable_tiktok:
                raise PipelineError("Publishing mode requested but no platform is enabled")
            if settings.enable_youtube:
                publications.append(YouTubePublisher().publish(video, content, privacy))
            if settings.enable_tiktok:
                publications.append(TikTokPublisher().publish(video, content, privacy))
        status = "published" if publications else "validated"
        item = _history_item(
            date_key,
            content,
            status=status,
            validation=validation,
            footage=clips,
            publications=publications,
        )
        if not fixture:
            history.upsert(item)
        result = {"status": status, "video": str(video), "metadata": str(work / "metadata.json"), **item}
        atomic_json(settings.output_dir / "last_run.json", result)
        return result
    except Exception as exc:
        item = _history_item(date_key, content, status="failed", footage=clips, publications=publications, error=str(exc))
        if not fixture:
            history.upsert(item)
        atomic_json(settings.output_dir / "last_run.json", item)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate, validate, and optionally publish one history Short")
    parser.add_argument("--fixture", action="store_true", help="Run deterministic offline render QA")
    parser.add_argument("--date", help="Production date in YYYY-MM-DD")
    args = parser.parse_args()
    selected_date = date.fromisoformat(args.date) if args.date else None
    try:
        result = run_pipeline(fixture=args.fixture, production_date=selected_date)
    except Exception as exc:
        print(f"PIPELINE FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": result["status"], "video": result["video"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
