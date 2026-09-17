from pathlib import Path

import pytest

from faceless_machine.content import ContentEngine, ContentError, subtitle_chunks
from faceless_machine.history import ContentHistory
from faceless_machine.models import Source
from faceless_machine.tts import srt_time


def test_subtitle_chunks_are_short_and_complete() -> None:
    script = "A surprising thing happened in Rome. It changed the city for generations. Nobody expected it."
    chunks = subtitle_chunks(script, max_words=5)
    assert " ".join(chunks) == script
    assert all(len(chunk.split()) <= 7 for chunk in chunks)
    assert len(chunks) >= 3


def test_srt_time_rounding() -> None:
    assert srt_time(0) == "00:00:00,000"
    assert srt_time(61.234) == "00:01:01,234"


class FakeGemini:
    def __init__(self, drafts: list[dict[str, object]]):
        self.drafts = drafts
        self.prompts: list[str] = []

    def generate_json(self, prompt: str, schema: dict[str, object], **_: object) -> dict[str, object]:
        self.prompts.append(prompt)
        if "verified" in schema.get("properties", {}):
            return {
                "verified": True,
                "unsupported_claims": [],
                "misleading_hook": False,
                "notes": "All claims are present in the supplied evidence.",
            }
        return self.drafts.pop(0)


def _draft(script: str) -> dict[str, object]:
    hook = script.split(". ", 1)[0] + "."
    return {
        "topic": "A verified historical event",
        "hook": hook,
        "script": script,
        "historical_period": "1900",
        "main_person_or_event": "Verified event",
        "visual_queries": ["old city", "historic map", "archive paper", "stone building", "clock tower"],
        "youtube_title": "A Verified Historical Event #Shorts",
        "youtube_description": "Sources are recorded in metadata.",
        "tiktok_caption": "A verified historical event.",
        "hashtags": ["history", "facts", "shorts"],
    }


def _research() -> dict[str, object]:
    return {
        "topic": "A verified historical event",
        "historical_period": "1900",
        "main_person_or_event": "Verified event",
        "sources": [
            Source("https://example.org/one", "Source one", ["Every scripted fact."]),
            Source("https://example.net/two", "Source two", ["Every scripted fact."]),
        ],
    }


def test_content_engine_retries_a_short_script(tmp_path: Path) -> None:
    short = "This draft is far too short. It cannot pass the deterministic word count requirement."
    valid = (
        "A verified event changed this city overnight. Contemporary records describe how residents "
        "gathered before sunrise and watched officials open the central square. Witnesses recorded "
        "the order of events, the public announcement, and the response from the crowd. The moment "
        "did not involve magic or legend; surviving documents explain exactly what happened. By noon, "
        "news had moved beyond the city, and later accounts preserved the same essential details. "
        "That evidence is why historians can still reconstruct the event with unusual confidence today."
    )
    gemini = FakeGemini([_draft(short), _draft(valid)])
    package = ContentEngine(gemini, ContentHistory(tmp_path / "history.json")).create(_research())

    assert 70 <= len(package.script.split()) <= 120
    assert len(gemini.prompts) == 3  # two drafts plus one fact-check call
    assert "previous draft was rejected" in gemini.prompts[1]


def test_content_engine_stops_after_retry_limit(tmp_path: Path) -> None:
    short = "This is still much too short."
    gemini = FakeGemini([_draft(short), _draft(short)])

    with pytest.raises(ContentError, match="after 2 attempts"):
        ContentEngine(gemini, ContentHistory(tmp_path / "history.json")).create(
            _research(), attempts=2
        )
