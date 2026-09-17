from pathlib import Path

from faceless_machine.history import ContentHistory


def test_history_upsert_and_duplicate_detection(tmp_path: Path) -> None:
    history = ContentHistory(tmp_path / "history.json")
    history.upsert(
        {
            "date": "2026-09-17",
            "topic": "The Great Molasses Flood",
            "main_person_or_event": "Boston 1919",
            "hook": "Boston was hit by a wave of molasses.",
            "status": "validated",
        }
    )
    assert len(history.items) == 1
    assert history.is_duplicate("Great Molasses Flood", "Boston 1919")
    history.upsert({"date": "2026-09-17", "status": "published"})
    assert len(history.items) == 1
    assert history.items[0]["status"] == "published"


def test_distinct_topic_is_not_duplicate(tmp_path: Path) -> None:
    history = ContentHistory(tmp_path / "history.json")
    history.upsert(
        {
            "date": "2026-09-17",
            "topic": "Pompeii thermopolium",
            "main_person_or_event": "Pompeii",
        }
    )
    assert not history.is_duplicate("Anglo-Zanzibar War", "Zanzibar")
