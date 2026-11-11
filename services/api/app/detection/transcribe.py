"""Speech recognition with faster-whisper (word-level timestamps, punctuation, VAD)."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import get_settings

log = logging.getLogger("clipx.asr")

_model: Any = None
_lock = threading.Lock()


@dataclass
class Word:
    w: str
    start: float
    end: float
    p: float

    def as_dict(self) -> dict[str, Any]:
        return {"w": self.w, "start": round(self.start, 3), "end": round(self.end, 3), "p": round(self.p, 3)}


@dataclass
class Transcript:
    words: list[Word]
    language: str | None
    engine: str

    @property
    def text(self) -> str:
        return "".join(w.w for w in self.words).strip()

    def between(self, start: float, end: float) -> list[Word]:
        return [w for w in self.words if w.end > start and w.start < end]


def get_model() -> Any:
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel

            s = get_settings()
            log.info("loading faster-whisper model %s", s.whisper_model)
            _model = WhisperModel(s.whisper_model, device=s.whisper_device, compute_type=s.whisper_compute_type)
        return _model


def engine_name() -> str:
    return f"faster-whisper:{get_settings().whisper_model}"


def transcribe(path: Path, *, offset: float = 0.0, start: float | None = None,
               duration: float | None = None) -> Transcript:
    """Transcribe a media file. Word times are returned relative to the file plus ``offset``."""
    from app.detection.signals import SR, load_audio

    audio = load_audio(path, start=start, duration=duration)
    if len(audio) < SR // 2:
        return Transcript([], None, engine_name())
    model = get_model()
    segments, info = model.transcribe(
        audio,
        language=get_settings().whisper_language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        condition_on_previous_text=False,
        beam_size=5,
    )
    words: list[Word] = []
    for seg in segments:
        for w in seg.words or []:
            words.append(Word(w.word, float(w.start) + offset, float(w.end) + offset, float(w.probability)))
    return Transcript(words, getattr(info, "language", None), engine_name())
