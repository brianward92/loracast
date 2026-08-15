from __future__ import annotations

import importlib
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

DEFAULT_ASR_CHUNK_SECONDS = 120
DEFAULT_FASTER_WHISPER_MODEL = "base.en"
_WHISPER_DOWNLOAD_ATTEMPTS = 4
_WHISPER_DOWNLOAD_BACKOFF_SECONDS = 5.0


def load_whisper_model(model_name: str | None) -> object | None:
    """Load a faster-whisper WhisperModel.

    Returns None if the package is missing. Tries the local HF cache first so
    a successful prior download survives flaky networks; only falls back to
    downloading from the Hub when the model isn't already on disk. The Hub
    path is retried with backoff because faster-whisper surfaces transient
    SSL errors from huggingface_hub as hard exceptions.
    """
    module = _load_faster_whisper()
    if module is None:
        return None

    name = model_name or DEFAULT_FASTER_WHISPER_MODEL
    common_kwargs = dict(device="cpu", compute_type="int8")

    # Fast path: model already in the HF cache → never touch the network.
    try:
        return module.WhisperModel(name, local_files_only=True, **common_kwargs)
    except Exception:  # noqa: BLE001 — faster-whisper raises ValueError, OSError, etc.
        pass

    # Slow path: download with retries to ride out HF Hub SSL hiccups.
    last_exc: Exception | None = None
    for attempt in range(1, _WHISPER_DOWNLOAD_ATTEMPTS + 1):
        try:
            return module.WhisperModel(name, **common_kwargs)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == _WHISPER_DOWNLOAD_ATTEMPTS:
                break
            time.sleep(_WHISPER_DOWNLOAD_BACKOFF_SECONDS * attempt)
    raise RuntimeError(
        f"failed to load faster_whisper model {name!r} after "
        f"{_WHISPER_DOWNLOAD_ATTEMPTS} download attempts: {last_exc}"
    ) from last_exc


def transcribe_audio(
    audio_path: Path,
    model: str | None = None,
    language: str = "en",
    chunk_seconds: int = DEFAULT_ASR_CHUNK_SECONDS,
    model_instance: object | None = None,
) -> dict:
    if model_instance is None:
        if _load_faster_whisper() is None:
            return {"error": "faster_whisper package not installed"}
        model_instance = load_whisper_model(model)
        if model_instance is None:
            return {"error": "faster_whisper package not installed"}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        chunk_paths = split_audio_for_asr(
            audio_path=audio_path, output_dir=tmp_dir, chunk_seconds=chunk_seconds
        )
        if not chunk_paths:
            chunk_paths = [audio_path]

        chunk_texts = []
        for index, chunk_path in enumerate(chunk_paths, start=1):
            try:
                segments, _info = model_instance.transcribe(
                    str(chunk_path),
                    language=language,
                    vad_filter=True,
                    # Anti-hallucination controls. Whisper is notorious for
                    # repetition loops on ad-reads, jingles, and quiet audio
                    # — especially with condition_on_previous_text=True, which
                    # lets prior hallucinations poison subsequent decoding.
                    # Tightening compression_ratio and log_prob thresholds
                    # also causes faster-whisper to drop runaway segments.
                    condition_on_previous_text=False,
                    compression_ratio_threshold=2.0,
                    log_prob_threshold=-1.0,
                    no_speech_threshold=0.6,
                )
            except Exception as exc:  # noqa: BLE001
                return {"error": f"failed on chunk {index}/{len(chunk_paths)}: {exc}"}

            text = " ".join(
                segment.text.strip() for segment in segments if segment.text.strip()
            ).strip()
            if text:
                chunk_texts.append(text)

        transcript_text = "\n\n".join(chunk_texts).strip()
        if not transcript_text:
            return {"error": "faster_whisper produced an empty transcript"}
        return {
            "content": transcript_text,
            "source_type": "asr_faster_whisper",
            "resolution_note": f"faster_whisper model={model or DEFAULT_FASTER_WHISPER_MODEL} chunks={len(chunk_paths)}",
            "is_machine_generated": True,
        }


def split_audio_for_asr(
    audio_path: Path, output_dir: Path, chunk_seconds: int
) -> list[Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or chunk_seconds <= 0:
        return [audio_path]

    chunk_dir = output_dir / "audio_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    extension = audio_path.suffix or ".mp3"
    pattern = chunk_dir / f"chunk_%03d{extension}"
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(audio_path),
        "-f",
        "segment",
        "-segment_time",
        str(chunk_seconds),
        "-c",
        "copy",
        str(pattern),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        return [audio_path]

    chunk_paths = sorted(chunk_dir.glob(f"*{extension}"))
    return chunk_paths or [audio_path]


def _load_faster_whisper():
    try:
        return importlib.import_module("faster_whisper")
    except ModuleNotFoundError:
        return None
