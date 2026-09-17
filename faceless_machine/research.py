from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import requests

from .history import ContentHistory
from .models import Source


USER_AGENT = "FacelessContentMachine/0.1 (history short research; noncommercial test)"


class ResearchError(RuntimeError):
    pass


class TopicResearcher:
    def __init__(self, seeds_path: Path, history: ContentHistory):
        self.seeds_path = seeds_path
        self.history = history
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def select(self, production_date: date) -> dict[str, Any]:
        seeds = json.loads(self.seeds_path.read_text(encoding="utf-8"))
        eligible = [
            seed
            for seed in seeds
            if not self.history.is_duplicate(seed["topic"], seed["main_person_or_event"])
        ]
        digest = hashlib.sha256(production_date.isoformat().encode()).hexdigest()
        start = int(digest[:8], 16)
        if eligible:
            for offset in range(len(eligible)):
                seed = eligible[(start + offset) % len(eligible)]
                sources = self._validate_seed_sources(seed)
                if len(sources) >= 2:
                    return {**seed, "sources": sources, "origin": "curated"}
        return self._from_wikimedia(production_date)

    def _validate_seed_sources(self, seed: dict[str, Any]) -> list[Source]:
        verified: list[Source] = []
        for raw in seed.get("sources", []):
            if self._url_is_reachable(raw["url"]):
                verified.append(
                    Source(
                        url=raw["url"],
                        title=raw["title"],
                        facts=list(raw["facts"]),
                        status="verified",
                    )
                )
        domains = {urlparse(source.url).netloc.lower() for source in verified}
        return verified if len(domains) >= 2 else []

    def _url_is_reachable(self, url: str) -> bool:
        try:
            response = self.session.get(url, timeout=20, allow_redirects=True, stream=True)
            return response.status_code < 400
        except requests.RequestException:
            return False

    def _from_wikimedia(self, production_date: date) -> dict[str, Any]:
        month_day = production_date.strftime("%m/%d")
        response = self.session.get(
            f"https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/events/{month_day}",
            timeout=30,
        )
        response.raise_for_status()
        events = response.json().get("events", [])
        for event in events:
            pages = event.get("pages") or []
            if not pages:
                continue
            page = pages[0]
            title = page.get("normalizedtitle") or page.get("title")
            text = str(event.get("text", "")).strip()
            if not title or len(text.split()) < 8 or self.history.is_duplicate(text, title):
                continue
            wiki_url = page.get("content_urls", {}).get("desktop", {}).get("page")
            wikibase = page.get("wikibase_item")
            if not wiki_url or not wikibase:
                continue
            wikidata_url = f"https://www.wikidata.org/wiki/{quote(str(wikibase))}"
            if not (self._url_is_reachable(wiki_url) and self._url_is_reachable(wikidata_url)):
                continue
            year = str(event.get("year", "historical"))
            return {
                "topic": text,
                "historical_period": year,
                "main_person_or_event": str(title),
                "facts": [text, str(page.get("description", "")).strip()],
                "sources": [
                    Source(wiki_url, f"Wikipedia: {title}", [text]),
                    Source(wikidata_url, f"Wikidata: {title}", [text]),
                ],
                "origin": "wikimedia_on_this_day",
            }
        raise ResearchError("No unused topic with two reachable evidence sources was found")
