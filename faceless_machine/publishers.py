from __future__ import annotations

import math
import os
import time
from pathlib import Path
from typing import Any

import requests

from .config import required_env
from .models import ContentPackage, PublishResult


class PublishError(RuntimeError):
    pass


def _response_json(response: requests.Response, label: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise PublishError(f"{label} returned a non-JSON response ({response.status_code})") from exc
    if not response.ok:
        detail = payload.get("error", payload)
        raise PublishError(f"{label} failed ({response.status_code}): {detail}")
    return payload


class YouTubePublisher:
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

    def __init__(self, session: requests.Session | None = None):
        self.client_id = required_env("YOUTUBE_CLIENT_ID")
        self.client_secret = required_env("YOUTUBE_CLIENT_SECRET")
        self.refresh_token = required_env("YOUTUBE_REFRESH_TOKEN")
        self.session = session or requests.Session()

    def _access_token(self) -> str:
        response = self.session.post(
            self.TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=45,
        )
        payload = _response_json(response, "YouTube token refresh")
        token = str(payload.get("access_token", ""))
        if not token:
            raise PublishError("YouTube token refresh did not return an access token")
        return token

    def publish(self, video: Path, content: ContentPackage, privacy: str) -> PublishResult:
        if privacy not in {"private", "public", "unlisted"}:
            raise PublishError(f"Unsupported YouTube privacy setting: {privacy}")
        token = self._access_token()
        tags = list(dict.fromkeys([*content.hashtags, "Shorts"]))[:20]
        body = {
            "snippet": {
                "title": content.youtube_title,
                "description": content.youtube_description,
                "tags": tags,
                "categoryId": "27",
                "defaultLanguage": "en",
                "defaultAudioLanguage": "en",
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": True,
            },
        }
        init = self.session.post(
            self.UPLOAD_URL,
            params={
                "uploadType": "resumable",
                "part": "snippet,status",
                "notifySubscribers": "false",
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(video.stat().st_size),
            },
            json=body,
            timeout=45,
        )
        if not init.ok:
            _response_json(init, "YouTube resumable upload initialization")
        upload_url = init.headers.get("Location")
        if not upload_url:
            raise PublishError("YouTube did not return a resumable upload URL")
        with video.open("rb") as handle:
            upload = self.session.put(
                upload_url,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(video.stat().st_size),
                },
                data=handle,
                timeout=600,
            )
        payload = _response_json(upload, "YouTube video upload")
        video_id = str(payload.get("id", ""))
        if not video_id:
            raise PublishError("YouTube upload succeeded without returning a video ID")
        return PublishResult("youtube", "published", video_id, privacy)


class TikTokPublisher:
    BASE_URL = "https://open.tiktokapis.com/v2"
    TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
    MIN_CHUNK = 5 * 1024 * 1024
    MAX_CHUNK = 64 * 1024 * 1024

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        direct_token = os.getenv("TIKTOK_ACCESS_TOKEN", "").strip()
        if direct_token:
            self.token = direct_token
        else:
            self.token = self._refresh_access_token()

    def _refresh_access_token(self) -> str:
        response = self.session.post(
            self.TOKEN_URL,
            data={
                "client_key": required_env("TIKTOK_CLIENT_KEY"),
                "client_secret": required_env("TIKTOK_CLIENT_SECRET"),
                "grant_type": "refresh_token",
                "refresh_token": required_env("TIKTOK_REFRESH_TOKEN"),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=45,
        )
        payload = _response_json(response, "TikTok token refresh")
        token = str(payload.get("access_token", ""))
        if not token:
            raise PublishError("TikTok token refresh did not return an access token")
        return token

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def creator_info(self) -> dict[str, Any]:
        response = self.session.post(
            f"{self.BASE_URL}/post/publish/creator_info/query/",
            headers=self.headers,
            json={},
            timeout=45,
        )
        payload = _response_json(response, "TikTok creator info")
        self._raise_api_error(payload, "TikTok creator info")
        return dict(payload.get("data", {}))

    def publish(self, video: Path, content: ContentPackage, privacy: str) -> PublishResult:
        privacy_level = "SELF_ONLY" if privacy == "private" else "PUBLIC_TO_EVERYONE"
        creator = self.creator_info()
        allowed = creator.get("privacy_level_options", [])
        if privacy_level not in allowed:
            raise PublishError(
                f"TikTok account does not allow privacy level {privacy_level}; available={allowed}"
            )
        size = video.stat().st_size
        chunk_size, chunk_count = self._chunk_plan(size)
        title = self._title(content)
        init = self.session.post(
            f"{self.BASE_URL}/post/publish/video/init/",
            headers=self.headers,
            json={
                "post_info": {
                    "title": title,
                    "privacy_level": privacy_level,
                    "disable_duet": True,
                    "disable_comment": False,
                    "disable_stitch": True,
                    "video_cover_timestamp_ms": 1000,
                    "brand_content_toggle": False,
                    "brand_organic_toggle": False,
                    "is_aigc": True,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": chunk_size,
                    "total_chunk_count": chunk_count,
                },
            },
            timeout=45,
        )
        payload = _response_json(init, "TikTok video initialization")
        self._raise_api_error(payload, "TikTok video initialization")
        data = payload.get("data", {})
        publish_id = str(data.get("publish_id", ""))
        upload_url = str(data.get("upload_url", ""))
        if not publish_id or not upload_url:
            raise PublishError("TikTok did not return publish_id and upload_url")
        self._upload(video, upload_url, chunk_size)
        status = self._wait_for_status(publish_id)
        return PublishResult("tiktok", status, publish_id, privacy)

    @classmethod
    def _chunk_plan(cls, size: int) -> tuple[int, int]:
        if size <= cls.MAX_CHUNK:
            return size, 1
        count = math.ceil(size / cls.MAX_CHUNK)
        chunk_size = math.ceil(size / count)
        if chunk_size < cls.MIN_CHUNK or chunk_size > cls.MAX_CHUNK:
            raise PublishError(f"Unable to produce a valid TikTok chunk plan for {size} bytes")
        return chunk_size, count

    def _upload(self, video: Path, upload_url: str, chunk_size: int) -> None:
        total = video.stat().st_size
        with video.open("rb") as handle:
            start = 0
            while start < total:
                data = handle.read(chunk_size)
                if not data:
                    raise PublishError("TikTok upload ended before the complete file was read")
                end = start + len(data) - 1
                response = self.session.put(
                    upload_url,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(data)),
                        "Content-Range": f"bytes {start}-{end}/{total}",
                    },
                    data=data,
                    timeout=300,
                )
                if not response.ok:
                    raise PublishError(
                        f"TikTok media upload failed ({response.status_code}): {response.text[:400]}"
                    )
                start = end + 1

    def _wait_for_status(self, publish_id: str) -> str:
        terminal_success = {"PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"}
        terminal_failure = {"FAILED", "PUBLISH_FAILED"}
        for _ in range(30):
            response = self.session.post(
                f"{self.BASE_URL}/post/publish/status/fetch/",
                headers=self.headers,
                json={"publish_id": publish_id},
                timeout=45,
            )
            payload = _response_json(response, "TikTok publish status")
            self._raise_api_error(payload, "TikTok publish status")
            status = str(payload.get("data", {}).get("status", ""))
            if status in terminal_success:
                return "published"
            if status in terminal_failure:
                reason = payload.get("data", {}).get("fail_reason", "unknown")
                raise PublishError(f"TikTok processing failed: {reason}")
            time.sleep(10)
        raise PublishError("TikTok processing did not complete within five minutes")

    @staticmethod
    def _raise_api_error(payload: dict[str, Any], label: str) -> None:
        error = payload.get("error") or {}
        code = str(error.get("code", "ok"))
        if code.lower() not in {"ok", "success", "0"}:
            raise PublishError(f"{label} API error {code}: {error.get('message', '')}")

    @staticmethod
    def _title(content: ContentPackage) -> str:
        hashtags = " ".join(f"#{tag}" for tag in content.hashtags)
        value = f"{content.tiktok_caption} {hashtags}".strip()
        return value[:2200]
