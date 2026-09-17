from __future__ import annotations

import os
import wave
from dataclasses import dataclass
from pathlib import Path

from piper import PiperVoice


class TTSError(RuntimeError):
    pass


@dataclass
class NarrationResult:
    audio_path: Path
    subtitles_path: Path
    duration: float
    cue_count: int


def srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


class PiperNarrator:
    def __init__(self, model_path: Path | None = None, config_path: Path | None = None):
        default_model = Path(os.getenv("PIPER_MODEL", ".cache/piper/en_US-lessac-medium.onnx"))
        self.model_path = (model_path or default_model).resolve()
        default_config = Path(os.getenv("PIPER_CONFIG", f"{self.model_path}.json"))
        self.config_path = (config_path or default_config).resolve()
        if not self.model_path.exists() or not self.config_path.exists():
            raise TTSError(
                "Piper voice files are missing. Run scripts/download_piper_voice.py before production."
            )
        self.voice = PiperVoice.load(str(self.model_path), config_path=str(self.config_path))

    def synthesize(self, chunks: list[str], output_dir: Path) -> NarrationResult:
        if not chunks:
            raise TTSError("No subtitle chunks were supplied for narration")
        output_dir.mkdir(parents=True, exist_ok=True)
        segment_paths: list[Path] = []
        segment_durations: list[float] = []
        for index, chunk in enumerate(chunks):
            path = output_dir / f"voice-{index:02d}.wav"
            with wave.open(str(path), "wb") as wav_file:
                self.voice.synthesize_wav(chunk, wav_file)
            with wave.open(str(path), "rb") as wav_file:
                segment_durations.append(wav_file.getnframes() / wav_file.getframerate())
            segment_paths.append(path)

        audio_path = output_dir / "narration.wav"
        silence_seconds = float(os.getenv("CAPTION_PAUSE_SECONDS", "0.07"))
        self._concatenate(segment_paths, audio_path, silence_seconds)
        subtitles_path = output_dir / "captions.srt"
        cursor = 0.0
        cues: list[str] = []
        for index, (chunk, duration) in enumerate(zip(chunks, segment_durations, strict=True), start=1):
            end = cursor + duration
            cues.append(f"{index}\n{srt_time(cursor)} --> {srt_time(end)}\n{chunk}\n")
            cursor = end + (silence_seconds if index < len(chunks) else 0.0)
        subtitles_path.write_text("\n".join(cues), encoding="utf-8")
        return NarrationResult(audio_path, subtitles_path, cursor, len(chunks))

    @staticmethod
    def _concatenate(paths: list[Path], output: Path, silence_seconds: float) -> None:
        params = None
        frames: list[bytes] = []
        for index, path in enumerate(paths):
            with wave.open(str(path), "rb") as source:
                current = source.getparams()
                comparable = (current.nchannels, current.sampwidth, current.framerate, current.comptype)
                if params is None:
                    params = current
                else:
                    expected = (params.nchannels, params.sampwidth, params.framerate, params.comptype)
                    if comparable != expected:
                        raise TTSError("Piper emitted inconsistent WAV parameters")
                frames.append(source.readframes(source.getnframes()))
                if index + 1 < len(paths):
                    silence_frames = round(source.getframerate() * silence_seconds)
                    frames.append(b"\x00" * silence_frames * source.getnchannels() * source.getsampwidth())
        if params is None:
            raise TTSError("No narration segments were produced")
        with wave.open(str(output), "wb") as target:
            target.setparams(params)
            for data in frames:
                target.writeframes(data)
