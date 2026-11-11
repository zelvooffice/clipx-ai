from __future__ import annotations

import numpy as np
import pytest

from app.detection import engine, scoring
from app.detection.context import ClaudeContextAnalyzer, ContextVerdict, HeuristicContextAnalyzer, default_analyzer
from app.detection.signals import AudioFeatures, ChatSecond, VisualFeatures, classify_message
from app.detection.transcribe import Word

W = {"storytelling_context": 0.30, "visual_interest": 0.25, "audio_speech": 0.15, "audience_reaction": 0.15,
     "engagement_potential": 0.15}


# ------------------------------------------------------------------------------ scoring
def test_weights_renormalise_over_available_signals() -> None:
    full = scoring.combine(scoring.ComponentScores(0.8, 0.8, 0.8, 0.8, 0.8), W)
    partial = scoring.combine(scoring.ComponentScores(storytelling_context=0.8, audio_speech=0.8), W)
    assert full.quality == pytest.approx(0.8) and partial.quality == pytest.approx(0.8)
    assert full.coverage == 1.0 and partial.coverage == pytest.approx(0.45)
    assert partial.confidence < full.confidence  # missing signals reduce confidence, not quality


def test_configured_weights_change_the_score() -> None:
    cs = scoring.ComponentScores(storytelling_context=1.0, visual_interest=0.0, audio_speech=0.0,
                                 audience_reaction=0.0, engagement_potential=0.0)
    assert scoring.combine(cs, W).quality == pytest.approx(0.30)
    assert scoring.combine(cs, {**W, "storytelling_context": 0.5, "visual_interest": 0.05}).quality == pytest.approx(
        0.5 / 1.0)


def test_disagreement_lowers_confidence() -> None:
    agree = scoring.combine(scoring.ComponentScores(0.7, 0.7, 0.7, 0.7, 0.7), W)
    disagree = scoring.combine(scoring.ComponentScores(1.0, 0.2, 1.0, 0.2, 0.7), W)
    assert disagree.confidence < agree.confidence


def _gate(cs: scoring.ComponentScores, **kw):  # type: ignore[no-untyped-def]
    res = scoring.combine(cs, W, analyzer_confidence=0.9)
    return scoring.gate(res, min_quality=kw.get("min_quality", 0.6), min_confidence=0.3,
                        accepted_so_far=kw.get("count", 0), target_max=kw.get("cap", 25))


def test_loudness_alone_never_accepted() -> None:
    g = _gate(scoring.ComponentScores(audio_speech=1.0, engagement_potential=1.0))
    assert not g.accepted and "independent signal families" in (g.reason or "")


def test_chat_alone_is_only_provisional() -> None:
    g = _gate(scoring.ComponentScores(audience_reaction=1.0, engagement_potential=1.0))
    assert not g.accepted and g.provisional


def test_two_families_accepted() -> None:
    assert _gate(scoring.ComponentScores(storytelling_context=0.9, visual_interest=0.8, audio_speech=0.7,
                                         engagement_potential=0.8)).accepted


def test_threshold_only_rises_near_cap_and_cap_is_hard() -> None:
    cs = scoring.ComponentScores(storytelling_context=0.66, visual_interest=0.66, audio_speech=0.66,
                                 engagement_potential=0.66)
    assert _gate(cs, count=0).accepted
    assert not _gate(cs, count=22).accepted  # stricter near the 25 cap
    strong = scoring.ComponentScores(1.0, 1.0, 1.0, 1.0, 1.0)
    assert not _gate(strong, count=25).accepted
    # never lowered to reach the target minimum: a weak moment stays rejected with zero clips so far
    weak = scoring.ComponentScores(0.3, 0.3, 0.3, 0.3, 0.3)
    assert not _gate(weak, count=0).accepted


# ------------------------------------------------------------------------------ duplicates
def test_dedupe_by_overlap_and_text() -> None:
    existing = [(100.0, 130.0, "no way bro did you see that that was insane")]
    assert scoring.is_duplicate((105.0, 132.0), "different words entirely here", existing)
    assert scoring.is_duplicate((300.0, 320.0), "no way bro did you see that that was insane", existing)
    assert scoring.is_duplicate((300.0, 320.0), "a totally new story about the weekend", existing) is None
    assert scoring.dedupe_key(101.0) == scoring.dedupe_key(103.9)


# ------------------------------------------------------------------------------ boundaries
def _words(text: str, start: float = 0.0, step: float = 0.4) -> list[Word]:
    out, t = [], start
    for w in text.split():
        out.append(Word(" " + w, t, t + step * 0.8, 0.9))
        t += step
    return out


def test_boundaries_snap_to_sentences() -> None:
    words = _words("okay so listen to this. my friend walks in with a cake. and then he trips on the cable! "
                   "everyone is screaming right now. anyway back to the game we go.", start=100.0)
    peak = 106.0  # "he trips"
    s, e, clean = engine.snap_boundaries(words, peak, None, None, min_s=5, max_s=60, lo=100.0, hi=130.0)
    assert clean
    inside = [i for i, w in enumerate(words) if w.start >= s - 1e-6]
    first = inside[0]
    assert first == 0 or words[first - 1].w.strip()[-1] in ".!?"  # never starts mid-sentence
    sentence_ends = [w.end for w in words if w.w.strip()[-1] in ".!?"]
    assert any(abs(e - 0.45 - x) < 0.01 for x in sentence_ends)  # ends on a sentence end (+ tail)
    assert s <= peak <= e


def test_boundaries_respect_max_length() -> None:
    words = _words(" ".join(["word"] * 200) + ".", start=0.0)
    s, e, _ = engine.snap_boundaries(words, 50.0, 0.0, 79.0, min_s=8, max_s=60, lo=0.0, hi=80.0)
    assert e - s <= 60.0 + 1e-6 and s <= 50.0 <= e


def test_boundaries_without_speech_still_valid() -> None:
    s, e, clean = engine.snap_boundaries([], 50.0, None, None, min_s=8, max_s=60, lo=0.0, hi=80.0)
    assert not clean and 8 <= e - s <= 60


# ------------------------------------------------------------------------------ engine
def _timeline(with_visual: bool = True, with_chat: bool = True, speech: bool = True) -> engine.Timeline:
    n = 60
    rms = np.full(n, -30.0, np.float32)
    rms[30:34] = -8.0  # loud reaction at t0+30
    burst = np.zeros(n, np.float32)
    burst[30:34] = 0.5
    audio = AudioFeatures(rms, np.zeros(n, np.float32), burst, np.full(n, 0.6, np.float32))
    visual = None
    if with_visual:
        motion = np.full(n, 0.02, np.float32)
        motion[29:33] = 0.3
        cuts = np.zeros(n, np.float32)
        cuts[30] = 1
        visual = VisualFeatures(motion, cuts, np.zeros(n, np.float32), np.full(n, 0.05, np.float32))
    chat_secs = None
    if with_chat:
        chat_secs = [ChatSecond(2, 0, 0, 0, 2) for _ in range(n)]
        for i in range(31, 37):
            chat_secs[i] = ChatSecond(25, 12, 6, 2, 20)
    words = _words("so we are just walking through the map. then this guy comes out of nowhere. "
                   "no way! what was that! that was the funniest thing I have seen today.", start=1012.0, step=0.5) \
        if speech else []
    return engine.Timeline(t0=1000.0, audio=audio, visual=visual, chat=chat_secs, words=words)


def test_find_peaks_locates_the_moment() -> None:
    peaks = engine.find_peaks(_timeline().finalize())
    assert peaks and abs(peaks[0] - 1031.0) <= 2


def _evaluate(tl: engine.Timeline, analyzer=None, **kw):  # type: ignore[no-untyped-def]
    return engine.evaluate(tl, 1031.0, analyzer=analyzer or HeuristicContextAnalyzer(), weights=W,
                           min_quality=kw.get("min_quality", 0.45), min_confidence=0.1, min_clip_s=8, max_clip_s=60,
                           accepted_so_far=0, target_max=25, existing=kw.get("existing", []))


def test_multisignal_moment_accepted_with_full_metadata() -> None:
    r = _evaluate(_timeline())
    assert r.status == "accepted", r.reason
    assert r.verdict.category == "funny"
    assert {"speech", "visual"} <= set(r.score.supporting_families) or len(r.score.supporting_families) >= 2
    assert 8 <= r.end - r.start <= 60 and r.start <= 1031 <= r.end
    assert r.verdict.explanation and r.transcript_excerpt and r.score.confidence > 0
    assert r.latency_ms >= 0


def test_same_moment_twice_is_duplicate() -> None:
    first = _evaluate(_timeline())
    second = _evaluate(_timeline(), existing=[(first.start, first.end, first.transcript_excerpt)])
    assert second.status == "duplicate"


def test_audio_only_spike_rejected() -> None:
    tl = _timeline(with_visual=False, with_chat=False, speech=False)
    r = _evaluate(tl)
    assert r.status == "rejected"


class _StubLLM:
    def __init__(self, is_highlight: bool) -> None:
        self.ok = is_highlight

    def analyze(self, window):  # type: ignore[no-untyped-def]
        return ContextVerdict(self.ok, "reaction", "Title", "LLM explanation", "summary", 0.9, 0.85,
                              window.peak - 15, window.peak + 4, ["funniest"], 0.9, "llm")


def test_context_analysis_can_veto() -> None:
    assert _evaluate(_timeline(), analyzer=_StubLLM(False)).status == "rejected"
    assert _evaluate(_timeline(), analyzer=_StubLLM(True)).status == "accepted"


def test_claude_analyzer_parses_structured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.detection import context
    from app.detection.context import ContextWindow

    captured = {}

    def fake(system, user, schema, **kw):  # type: ignore[no-untyped-def]
        captured["schema"] = schema
        captured["user"] = user
        return {"is_highlight": True, "category": "argument", "title": "Lacy vs chat", "explanation": "e",
                "context_summary": "c", "storytelling_score": 1.7, "engagement_score": 0.6,
                "suggested_start": 10.0, "suggested_end": 30.0, "keywords": ["no"], "confidence": 0.8}

    monkeypatch.setattr(context.llm, "complete_json", fake)
    v = ClaudeContextAnalyzer().analyze(ContextWindow(_words("hello there."), 5.0, 0.0, 20.0, {"audio_z": 2}))
    assert v.analyzer == "llm" and v.category == "argument" and v.storytelling_score == 1.0  # clamped
    assert captured["schema"]["additionalProperties"] is False
    assert "hello there." in captured["user"]


def test_llm_unavailable_falls_back_to_heuristic(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    from app.detection import context
    from app.services.llm import LLMUnavailable

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    get_settings.cache_clear()

    def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise LLMUnavailable("down")

    monkeypatch.setattr(context.llm, "complete_json", boom)
    r = _evaluate(_timeline(), analyzer=default_analyzer(True))
    assert r.verdict.analyzer == "heuristic"


def test_chat_message_classification() -> None:
    assert classify_message("KEKW KEKW") == (True, False, False)
    assert classify_message("CLIP IT") == (False, True, True)
    assert classify_message("hello") == (False, False, False)
