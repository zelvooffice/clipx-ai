"""Real speech recognition + subtitle synchronisation on real synthesized speech (espeak-ng)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.video import subtitles


@pytest.mark.media
def test_whisper_word_timestamps_drive_subtitles(speech_video: Path) -> None:
    try:
        from app.detection.transcribe import transcribe

        tr = transcribe(speech_video)
    except Exception as exc:  # model download blocked etc. — report as skip, never fake
        pytest.skip(f"faster-whisper model unavailable: {exc}")
    text = tr.text.lower()
    for word in ("hello", "chat", "test", "clipping", "subtitles", "word"):
        assert word in text, text
    times = [(w.start, w.end) for w in tr.words]
    assert all(s <= e for s, e in times) and all(a[0] <= b[0] for a, b in zip(times, times[1:], strict=False))
    cues = subtitles.build_cues([w.as_dict() for w in tr.words], clip_end=times[-1][1] + 1)
    first_cue_words = " ".join(w["w"] for w in cues[0].words).lower()
    assert "hello" in first_cue_words
    assert cues[0].start == pytest.approx(tr.words[0].start, abs=1e-3)  # captions start exactly with speech
    assert cues[0].start < 1.5
