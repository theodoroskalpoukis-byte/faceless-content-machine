from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

from .config import required_env


class GeminiError(RuntimeError):
    pass


class GeminiClient:
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
    PREFERRED_MODELS = (
        "gemini-3.5-flash-lite",
        "gemini-3.7-flash",
        "gemini-3.5-flash",
    )

    def __init__(self, api_key: str | None = None, timeout: int = 90):
        self.api_key = api_key or required_env("GEMINI_API_KEY")
        self.timeout = timeout
        self.model = self._select_model()

    def _select_model(self) -> str:
        response = requests.get(
            f"{self.BASE_URL}/models",
            params={"key": self.api_key, "pageSize": 1000},
            timeout=self.timeout,
        )
        response.raise_for_status()
        available: list[str] = []
        for item in response.json().get("models", []):
            methods = item.get("supportedGenerationMethods", [])
            if "generateContent" in methods:
                available.append(str(item.get("name", "")).removeprefix("models/"))
        requested = os.getenv("GEMINI_MODEL", "").strip()
        if requested:
            if requested not in available:
                raise GeminiError(f"Configured GEMINI_MODEL is unavailable: {requested}")
            return requested
        for model in self.PREFERRED_MODELS:
            if model in available:
                return model
        flash = [name for name in available if "flash" in name and "live" not in name]
        if not flash:
            raise GeminiError("No generateContent Flash model is available for this API key")
        return sorted(flash, reverse=True)[0]

    def generate_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        temperature: float = 0.35,
        attempts: int = 3,
    ) -> dict[str, Any]:
        url = f"{self.BASE_URL}/models/{self.model}:generateContent"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema,
                "temperature": temperature,
                "maxOutputTokens": 1800,
            },
        }
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = requests.post(
                    url,
                    params={"key": self.api_key},
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                result = json.loads(text)
                if not isinstance(result, dict):
                    raise GeminiError("Gemini returned a non-object JSON response")
                return result
            except (KeyError, IndexError, json.JSONDecodeError, requests.RequestException, GeminiError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(2**attempt)
        raise GeminiError(f"Gemini JSON generation failed after {attempts} attempts: {last_error}")
