from __future__ import annotations

import json
import random
import subprocess
from pathlib import Path

import requests

from .config import required_env
from .models import FootageClip


class FootageError(RuntimeError):
    pass


class PexelsClient:
    SEARCH_URL = "https://api.pexels.com/v1/videos/search"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or required_env("PEXELS_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": self.api_key})

    def acquire(
        self,
        queries: list[str],
        output_dir: Path,
        *,
        target_count: int = 6,
        seed: str = "",
    ) -> list[FootageClip]:
        output_dir.mkdir(parents=True, exist_ok=True)
        rng = random.Random(seed)
        used_ids: set[int] = set()
        clips: list[FootageClip] = []
        for query in queries:
            response = self.session.get(
                self.SEARCH_URL,
                params={"query": query, "per_page": 20, "orientation": "portrait", "size": "medium"},
                timeout=45,
            )
            response.raise_for_status()
            remaining = response.headers.get("X-Ratelimit-Remaining")
            if remaining is not None and int(remaining) < 1:
                raise FootageError("Pexels monthly API quota is exhausted; refusing to continue")
            videos = list(response.json().get("videos", []))
            rng.shuffle(videos)
            for video in videos:
                video_id = int(video.get("id", 0))
                if not video_id or video_id in used_ids:
                    continue
                files = [
                    item
                    for item in video.get("video_files", [])
                    if item.get("link") and item.get("file_type") == "video/mp4"
                ]
                files.sort(
                    key=lambda item: (
                        int(item.get("height") or 0) >= int(item.get("width") or 0),
                        min(int(item.get("height") or 0), 1920) * min(int(item.get("width") or 0), 1080),
                    ),
                    reverse=True,
                )
                for file_info in files:
                    path = output_dir / f"pexels-{video_id}.mp4"
                    try:
                        media = self.session.get(file_info["link"], timeout=150, stream=True)
                        media.raise_for_status()
                        with path.open("wb") as handle:
                            for chunk in media.iter_content(chunk_size=1024 * 1024):
                                if chunk:
                                    handle.write(chunk)
                        self._validate_video(path)
                    except (requests.RequestException, subprocess.SubprocessError, FootageError):
                        path.unlink(missing_ok=True)
                        continue
                    used_ids.add(video_id)
                    clips.append(
                        FootageClip(
                            pexels_id=video_id,
                            query=query,
                            source_url=str(video.get("url", "")),
                            creator=str(video.get("user", {}).get("name", "Pexels creator")),
                            file_path=str(path),
                        )
                    )
                    break
                if clips and clips[-1].pexels_id == video_id:
                    break
            if len(clips) >= target_count:
                break
        if len(clips) < 4:
            raise FootageError(f"Only {len(clips)} valid distinct clips were found; at least 4 are required")
        return clips

    @staticmethod
    def _validate_video(path: Path) -> None:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_type,width,height,duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        streams = json.loads(result.stdout).get("streams", [])
        if not streams or int(streams[0].get("width", 0)) < 360 or int(streams[0].get("height", 0)) < 360:
            raise FootageError(f"Downloaded Pexels asset is not a usable video: {path}")
