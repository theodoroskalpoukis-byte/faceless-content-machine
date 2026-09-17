import json
from pathlib import Path

import pytest

from faceless_machine.config import Settings
from faceless_machine.pipeline import run_pipeline


@pytest.mark.skipif(not Path("/usr/bin/ffmpeg").exists(), reason="ffmpeg is required")
def test_fixture_pipeline_renders_valid_vertical_video(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PUBLISH_MODE", "dry-run")
    monkeypatch.delenv("ENABLE_YOUTUBE", raising=False)
    monkeypatch.delenv("ENABLE_TIKTOK", raising=False)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls, root=None: Settings(
        root=tmp_path,
        output_dir=tmp_path / "output",
        history_path=tmp_path / "data" / "content_history.json",
        seeds_path=tmp_path / "data" / "topic_seeds.json",
        publish_mode="dry-run",
        enable_youtube=False,
        enable_tiktok=False,
        allow_public_publish=False,
    )))
    result = run_pipeline(fixture=True)
    assert result["status"] == "validated"
    assert Path(result["video"]).exists()
    report = json.loads((tmp_path / "output" / "last_run.json").read_text())
    assert report["validation"]["width"] == 1080
    assert report["validation"]["height"] == 1920
