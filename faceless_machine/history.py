from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


def normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


class ContentHistory:
    def __init__(self, path: Path):
        self.path = path
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "items": []}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
            raise ValueError("content history has an invalid schema")
        return raw

    @property
    def items(self) -> list[dict[str, Any]]:
        return self.data["items"]

    def is_duplicate(self, topic: str, main_person: str = "", threshold: float = 0.78) -> bool:
        candidate = normalize(f"{topic} {main_person}")
        for item in self.items:
            prior = normalize(
                f"{item.get('topic', '')} {item.get('main_person_or_event', '')}"
            )
            if candidate == prior or SequenceMatcher(None, candidate, prior).ratio() >= threshold:
                return True
        return False

    def recent_hooks(self, limit: int = 40) -> list[str]:
        return [str(item.get("hook", "")) for item in self.items[-limit:] if item.get("hook")]

    def recent_visual_queries(self, limit: int = 60) -> list[str]:
        queries: list[str] = []
        for item in self.items[-limit:]:
            queries.extend(item.get("visual_queries", []))
        return queries

    def find_date(self, production_date: str) -> dict[str, Any] | None:
        return next((item for item in self.items if item.get("date") == production_date), None)

    def upsert(self, item: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        item = {**item, "updated_at": now}
        existing = self.find_date(str(item["date"]))
        if existing is None:
            item.setdefault("created_at", now)
            self.items.append(item)
        else:
            existing.update(item)
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)
