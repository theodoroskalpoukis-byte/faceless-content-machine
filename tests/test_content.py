from faceless_machine.content import subtitle_chunks
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
