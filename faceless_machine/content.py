from __future__ import annotations

import re
from typing import Any

from .gemini import GeminiClient
from .history import ContentHistory, normalize
from .models import ContentPackage, Source


class ContentError(RuntimeError):
    pass


CONTENT_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "topic": {"type": "STRING"},
        "hook": {"type": "STRING"},
        "script": {"type": "STRING"},
        "historical_period": {"type": "STRING"},
        "main_person_or_event": {"type": "STRING"},
        "visual_queries": {"type": "ARRAY", "items": {"type": "STRING"}},
        "youtube_title": {"type": "STRING"},
        "youtube_description": {"type": "STRING"},
        "tiktok_caption": {"type": "STRING"},
        "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": [
        "topic",
        "hook",
        "script",
        "historical_period",
        "main_person_or_event",
        "visual_queries",
        "youtube_title",
        "youtube_description",
        "tiktok_caption",
        "hashtags",
    ],
}


VERIFY_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "verified": {"type": "BOOLEAN"},
        "unsupported_claims": {"type": "ARRAY", "items": {"type": "STRING"}},
        "misleading_hook": {"type": "BOOLEAN"},
        "notes": {"type": "STRING"},
    },
    "required": ["verified", "unsupported_claims", "misleading_hook", "notes"],
}


def subtitle_chunks(script: str, max_words: int = 7) -> list[str]:
    words = script.replace("\n", " ").split()
    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        current.append(word)
        boundary = word.endswith((".", "!", "?", ";", ":", ","))
        if len(current) >= max_words or (boundary and len(current) >= 3):
            chunks.append(" ".join(current))
            current = []
    if current:
        if chunks and len(current) < 3:
            chunks[-1] = f"{chunks[-1]} {' '.join(current)}"
        else:
            chunks.append(" ".join(current))
    return chunks


class ContentEngine:
    def __init__(self, gemini: GeminiClient, history: ContentHistory):
        self.gemini = gemini
        self.history = history

    def create(self, research: dict[str, Any]) -> ContentPackage:
        sources: list[Source] = research["sources"]
        evidence = "\n".join(
            f"SOURCE {index + 1}: {source.title}\nURL: {source.url}\nFACTS:\n"
            + "\n".join(f"- {fact}" for fact in source.facts if fact)
            for index, source in enumerate(sources)
        )
        prompt = f"""You produce an English 9:16 history Short for a general international audience.

Use ONLY the evidence below. Never add a date, number, quote, motive, causal claim, or superlative that is absent from the evidence. If the evidence cannot support a compelling accurate story, keep the story narrower.

Topic: {research['topic']}
Period: {research['historical_period']}
Main person/event: {research['main_person_or_event']}

EVIDENCE
{evidence}

Requirements:
- 70-120 spoken words.
- The script's first sentence must be the hook verbatim.
- Hook must create a truthful information gap in 1-2 seconds, not generic clickbait.
- Fast, simple spoken English; one story; clear payoff.
- 5-7 concrete stock-video search queries. Do not request copyrighted films, reenactments, logos, or specific celebrities.
- YouTube title <= 90 characters. Description must include a short source list.
- TikTok caption <= 300 characters before hashtags.
- 3-6 relevant hashtags without # characters.
- Do not repeat these recent hooks: {self.history.recent_hooks()}
- Avoid these recent footage searches: {self.history.recent_visual_queries()}
"""
        raw = self.gemini.generate_json(prompt, CONTENT_SCHEMA, temperature=0.45)
        package = ContentPackage(
            topic=str(raw["topic"]).strip(),
            hook=str(raw["hook"]).strip(),
            script=str(raw["script"]).strip(),
            historical_period=str(raw["historical_period"]).strip(),
            main_person_or_event=str(raw["main_person_or_event"]).strip(),
            sources=sources,
            visual_queries=[str(value).strip() for value in raw["visual_queries"] if str(value).strip()],
            youtube_title=str(raw["youtube_title"]).strip(),
            youtube_description=str(raw["youtube_description"]).strip(),
            tiktok_caption=str(raw["tiktok_caption"]).strip(),
            hashtags=[re.sub(r"[^A-Za-z0-9_]", "", str(tag).lstrip("#")) for tag in raw["hashtags"]],
        )
        package.subtitle_chunks = subtitle_chunks(package.script)
        self._validate_structure(package)
        package.verification = self._verify(package, evidence)
        if not package.verification["verified"] or package.verification["misleading_hook"]:
            raise ContentError(f"Fact verification rejected the script: {package.verification}")
        return package

    def _validate_structure(self, package: ContentPackage) -> None:
        words = package.script.split()
        if not 70 <= len(words) <= 120:
            raise ContentError(f"Script must be 70-120 words, got {len(words)}")
        first_sentence = re.split(r"(?<=[.!?])\s+", package.script, maxsplit=1)[0]
        if normalize(first_sentence) != normalize(package.hook):
            raise ContentError("Script does not begin with the exact hook")
        if not 5 <= len(package.visual_queries) <= 7:
            raise ContentError("Expected 5-7 visual queries")
        if len(package.youtube_title) > 90:
            raise ContentError("YouTube title is too long")
        if len(package.sources) < 2:
            raise ContentError("At least two verified sources are required")
        if len(package.subtitle_chunks) < 5:
            raise ContentError("Subtitle chunking produced too few captions")
        if self.history.is_duplicate(package.topic, package.main_person_or_event):
            raise ContentError("Generated topic duplicates content history")

    def _verify(self, package: ContentPackage, evidence: str) -> dict[str, Any]:
        prompt = f"""Act as a strict historical fact checker. Compare every factual statement in the hook and script to the supplied evidence. Reject implied facts, unsupported details, fake quotations, overclaiming, and a hook whose payoff is weaker than promised.

EVIDENCE
{evidence}

HOOK
{package.hook}

SCRIPT
{package.script}

Return verified=true only when every material claim is directly supported by the evidence. If uncertain, reject.
"""
        result = self.gemini.generate_json(prompt, VERIFY_SCHEMA, temperature=0.0)
        result["verified"] = bool(result.get("verified")) and not result.get("unsupported_claims")
        result["misleading_hook"] = bool(result.get("misleading_hook"))
        return result
