from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    pass


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Required secret {name} is not configured")
    return value


@dataclass(frozen=True)
class Settings:
    root: Path
    output_dir: Path
    history_path: Path
    seeds_path: Path
    publish_mode: str
    enable_youtube: bool
    enable_tiktok: bool
    allow_public_publish: bool
    min_duration: float = 25.0
    max_duration: float = 45.0
    width: int = 1080
    height: int = 1920
    fps: int = 30

    @classmethod
    def from_env(cls, root: Path | None = None) -> "Settings":
        project_root = (root or Path(__file__).resolve().parents[1]).resolve()
        publish_mode = os.getenv("PUBLISH_MODE", "dry-run").strip().lower()
        if publish_mode not in {"dry-run", "private", "public"}:
            raise ConfigError("PUBLISH_MODE must be dry-run, private, or public")
        allow_public = env_bool("ALLOW_PUBLIC_PUBLISH")
        if publish_mode == "public" and not allow_public:
            raise ConfigError(
                "Public publishing is locked. Set ALLOW_PUBLIC_PUBLISH=true only after QA."
            )
        return cls(
            root=project_root,
            output_dir=project_root / "output",
            history_path=project_root / "data" / "content_history.json",
            seeds_path=project_root / "data" / "topic_seeds.json",
            publish_mode=publish_mode,
            enable_youtube=env_bool("ENABLE_YOUTUBE"),
            enable_tiktok=env_bool("ENABLE_TIKTOK"),
            allow_public_publish=allow_public,
        )
