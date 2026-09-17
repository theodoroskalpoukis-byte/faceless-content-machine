from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

import requests


VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json?download=true"
FILE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{path}?download=true"


def _digest(path: Path) -> str:
    value = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _download(path: str, destination: Path, expected_size: int, expected_md5: str) -> None:
    if destination.exists() and destination.stat().st_size == expected_size:
        if _digest(destination) == expected_md5:
            return
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    for _ in range(4):
        start = temporary.stat().st_size if temporary.exists() else 0
        headers = {"Range": f"bytes={start}-"} if start else {}
        with requests.get(FILE_URL.format(path=path), headers=headers, stream=True, timeout=120) as response:
            response.raise_for_status()
            if start and response.status_code != 206:
                temporary.unlink(missing_ok=True)
                start = 0
            with temporary.open("ab" if start else "wb") as handle:
                for chunk in response.iter_content(1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        if temporary.stat().st_size == expected_size and _digest(temporary) == expected_md5:
            temporary.replace(destination)
            return
        if temporary.stat().st_size > expected_size:
            temporary.unlink()
    actual = temporary.stat().st_size if temporary.exists() else 0
    raise RuntimeError(
        f"Voice download failed integrity validation: expected {expected_size} bytes, got {actual}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Download the pinned Piper voice used by production")
    parser.add_argument("--directory", default=".cache/piper")
    parser.add_argument("--voice", default="en_US-lessac-medium")
    args = parser.parse_args()
    destination = Path(args.directory).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.voice):
        raise ValueError("Invalid Piper voice name")
    response = requests.get(VOICES_URL, timeout=60)
    response.raise_for_status()
    voice = response.json().get(args.voice)
    if not voice:
        raise ValueError(f"Piper voice does not exist: {args.voice}")
    wanted = {
        path: metadata
        for path, metadata in voice["files"].items()
        if path.endswith(".onnx") or path.endswith(".onnx.json")
    }
    for remote_path, metadata in wanted.items():
        _download(
            remote_path,
            destination / Path(remote_path).name,
            int(metadata["size_bytes"]),
            str(metadata["md5_digest"]),
        )
    model = destination / f"{args.voice}.onnx"
    config = destination / f"{args.voice}.onnx.json"
    if not model.exists() or not config.exists():
        raise RuntimeError(f"Piper did not create the expected files in {destination}")
    print(model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
