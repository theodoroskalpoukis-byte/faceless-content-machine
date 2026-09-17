from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Source:
    url: str
    title: str
    facts: list[str]
    status: str = "verified"


@dataclass
class ContentPackage:
    topic: str
    hook: str
    script: str
    historical_period: str
    main_person_or_event: str
    sources: list[Source]
    visual_queries: list[str]
    youtube_title: str
    youtube_description: str
    tiktok_caption: str
    hashtags: list[str]
    subtitle_chunks: list[str] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ContentPackage":
        data = dict(raw)
        data["sources"] = [Source(**source) for source in data.get("sources", [])]
        return cls(**data)


@dataclass
class FootageClip:
    pexels_id: int
    query: str
    source_url: str
    creator: str
    file_path: str


@dataclass
class PublishResult:
    platform: str
    status: str
    publication_id: str | None = None
    privacy: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
