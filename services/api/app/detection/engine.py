"""Highlight engine: peaks -> context -> boundaries -> scores -> gate -> dedupe.

Used by the live analyzer (rolling buffer), by chat-nominated clip verification, and by post-hoc analysis
of any authorized clip media. All times are seconds on one absolute timeline (stream offset).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.detection import scoring
from app.detection.context import ContextAnalyzer, ContextVerdict, ContextWindow
from app.detection.signals import AudioFeatures, ChatSecond, VisualFeatures, robust_z, squash
from app.detection.transcribe import Word

SENTENCE_END = re.compile(r"[.?!…]['\")\]]?$")


# ------------------------------------------------------------------------------------- timeline
@dataclass
class Timeline:
    """Per-second signals on the absolute timeline starting at ``t0``."""

    t0: float
    audio: AudioFeatures | None = None
    visual: VisualFeatures | None = None
    chat: list[ChatSecond] | None = None
    words: list[Word] = field(default_factory=list)
    audio_z: np.ndarray | None = None  # z vs stream baseline if available, else robust in-window z
    motion_z: np.ndarray | None = None
    chat_z: np.ndarray | None = None

    def finalize(self) -> Timeline:
        if self.audio is not None and self.audio_z is None:
            self.audio_z = robust_z(self.audio.rms_db)
        if self.visual is not None and self.motion_z is None:
            self.motion_z = robust_z(self.visual.motion)
        if self.chat is not None and self.chat_z is None:
            self.chat_z = robust_z(np.array([c.count for c in self.chat], dtype=np.float32))
        return self

    def idx(self, t: float) -> int:
        return int(t - self.t0)

    def span(self) -> float:
        n = [len(x) for x in (self.audio_z, self.motion_z, self.chat_z) if x is not None]
        return float(max(n)) if n else (self.words[-1].end - self.t0 if self.words else 0.0)


def _win(arr: np.ndarray | None, i0: int, i1: int) -> np.ndarray | None:
    if arr is None or len(arr) == 0:
        return None
    i0, i1 = max(0, i0), min(len(arr), i1)
    return arr[i0:i1] if i1 > i0 else None


def excitement_curve(tl: Timeline) -> np.ndarray:
    """Combined per-second 'something is happening' curve used only to *nominate* peaks."""
    parts, weights = [], []
    for arr, w in ((tl.audio_z, 0.3), (tl.motion_z, 0.3), (tl.chat_z, 0.4)):
        if arr is not None and len(arr):
            parts.append(np.clip(arr, 0, 6))
            weights.append(w)
    if not parts:
        return np.zeros(0, dtype=np.float32)
    n = min(len(p) for p in parts)
    curve = sum(w * p[:n] for p, w in zip(parts, weights, strict=True)) / sum(weights)
    if tl.audio is not None:
        curve = curve + 0.8 * tl.audio.burst[:n]
    return np.asarray(curve, dtype=np.float32)


def find_peaks(tl: Timeline, *, threshold: float = 1.0, min_gap_s: int = 15, edge_guard_s: int = 6
               ) -> list[float]:
    curve = excitement_curve(tl)
    if len(curve) < 3:
        return []
    k = np.ones(3) / 3
    smooth = np.convolve(curve, k, mode="same")
    order = np.argsort(smooth)[::-1]
    chosen: list[int] = []
    for i in order:
        if smooth[i] < threshold:
            break
        if i > len(smooth) - edge_guard_s:  # wait until the reaction after the peak is buffered
            continue
        if all(abs(i - j) >= min_gap_s for j in chosen):
            chosen.append(int(i))
    return [tl.t0 + i + 0.5 for i in sorted(chosen)]


# ------------------------------------------------------------------------------------- boundaries
def snap_boundaries(words: list[Word], peak: float, suggested_start: float | None, suggested_end: float | None,
                    *, min_s: float, max_s: float, lo: float, hi: float) -> tuple[float, float, bool]:
    """Snap to sentence boundaries. Returns (start, end, clean) where clean = both ends on boundaries."""
    start_guess = suggested_start if suggested_start is not None else peak - 14
    end_guess = suggested_end if suggested_end is not None else peak + 6
    start_guess = max(lo, min(start_guess, peak - 2))
    end_guess = min(hi, max(end_guess, peak + 2))
    ws = [w for w in words if lo <= w.start and w.end <= hi]
    if not ws:
        s, e = start_guess, end_guess
        return _fit(s, e, peak, min_s, max_s, lo, hi) + (False,)

    def is_sentence_start(i: int) -> bool:
        if i == 0:
            return True
        gap = ws[i].start - ws[i - 1].end
        return bool(SENTENCE_END.search(ws[i - 1].w.strip())) or gap > 0.7

    def is_sentence_end(i: int) -> bool:
        if i == len(ws) - 1:
            return True
        gap = ws[i + 1].start - ws[i].end
        return bool(SENTENCE_END.search(ws[i].w.strip())) or gap > 0.6

    starts = [i for i in range(len(ws)) if is_sentence_start(i) and ws[i].start <= peak]
    ends = [i for i in range(len(ws)) if is_sentence_end(i) and ws[i].end >= peak - 1]
    clean = bool(starts and ends)
    s = min((ws[i].start for i in starts), key=lambda t: abs(t - start_guess), default=start_guess)
    e = min((ws[i].end for i in ends), key=lambda t: abs(t - end_guess), default=end_guess)
    s, e = max(lo, s - 0.25), min(hi, e + 0.45)  # breathing room, reaction tail
    if e - s > max_s:  # too long: move the start forward to the latest sentence start that fits
        fitting = [ws[i].start for i in starts if e - ws[i].start <= max_s]
        s = max(lo, (min(fitting) if fitting else e - max_s) - 0.25)
        if e - s > max_s:
            s = e - max_s
    s, e = _fit(s, e, peak, min_s, max_s, lo, hi)
    return s, e, clean


def _fit(s: float, e: float, peak: float, min_s: float, max_s: float, lo: float, hi: float
         ) -> tuple[float, float]:
    if e - s < min_s:
        missing = min_s - (e - s)
        s, e = max(lo, s - missing * 0.6), min(hi, e + missing * 0.4)
        if e - s < min_s:
            s, e = max(lo, e - min_s), min(hi, max(e, s + min_s))
    if e - s > max_s:
        s = max(lo, min(peak - 3, e - max_s))
        e = min(e, s + max_s)
    return round(s, 3), round(e, 3)


# ------------------------------------------------------------------------------------- components
def signal_summary(tl: Timeline, peak: float) -> dict[str, Any]:
    i = tl.idx(peak)
    out: dict[str, Any] = {}
    a = _win(tl.audio_z, i - 2, i + 3)
    if a is not None and tl.audio is not None:
        out["audio_z"] = round(float(a.max()), 2)
        burst = _win(tl.audio.burst, i - 2, i + 3)
        out["audio_burst"] = round(float(np.max(burst)) if burst is not None else 0.0, 2)
    m = _win(tl.motion_z, i - 2, i + 3)
    if m is not None and tl.visual is not None:
        out["motion_z"] = round(float(m.max()), 2)
        cuts = _win(tl.visual.scene_cut, i - 3, i + 4)
        out["scene_cuts"] = int(np.sum(cuts)) if cuts is not None else 0
        fa = _win(tl.visual.face_area, i - 4, i + 4)
        out["face_visible"] = bool(fa is not None and fa.max() > 0)
    c = _win(tl.chat_z, i - 2, i + 6)
    if c is not None and tl.chat is not None:
        secs = tl.chat[max(0, i - 2): i + 6]
        total = sum(x.count for x in secs) or 1
        out["chat_z"] = round(float(c.max()), 2)
        out["chat_msgs"] = total
        out["chat_laugh_ratio"] = round(sum(x.laugh for x in secs) / total, 2)
        out["chat_hype_ratio"] = round(sum(x.hype for x in secs) / total, 2)
        out["chat_clip_calls"] = sum(x.clip_calls for x in secs)
    near = [w for w in tl.words if peak - 5 <= w.start <= peak + 5]
    out["speech_wps"] = round(len(near) / 10, 2)
    return out


def component_scores(tl: Timeline, peak: float, verdict: ContextVerdict) -> scoring.ComponentScores:
    sig = signal_summary(tl, peak)
    cs = scoring.ComponentScores()
    if "audio_z" in sig:
        wps = float(sig["speech_wps"])
        speech = min(1.0, wps / 3.0)
        cs.audio_speech = 0.45 * squash(sig["audio_z"], 1.0, 1.0) + 0.25 * min(1.0, sig["audio_burst"] * 2.5) \
            + 0.30 * speech
    if "motion_z" in sig and tl.visual is not None:
        i = tl.idx(peak)
        fa = _win(tl.visual.face_area, i - 5, i + 5)
        face_dyn = min(1.0, float(np.ptp(fa)) * 25) if fa is not None and len(fa) else 0.0
        bj = _win(tl.visual.brightness_jump, i - 2, i + 3)
        cs.visual_interest = (0.40 * squash(sig["motion_z"], 1.0, 1.0) + 0.20 * min(1.0, sig["scene_cuts"] / 2)
                              + 0.15 * min(1.0, float(bj.max()) * 6 if bj is not None else 0.0)
                              + 0.25 * face_dyn)
    if "chat_z" in sig:
        reaction = sig["chat_laugh_ratio"] + sig["chat_hype_ratio"] + min(0.3, sig["chat_clip_calls"] * 0.05)
        cs.audience_reaction = 0.6 * squash(sig["chat_z"], 1.5, 1.0) + 0.4 * min(1.0, reaction * 2)
    cs.storytelling_context = verdict.storytelling_score if tl.words else None
    cs.engagement_potential = verdict.engagement_score
    return cs


# ------------------------------------------------------------------------------------- evaluate
@dataclass
class MomentResult:
    peak: float
    start: float
    end: float
    verdict: ContextVerdict
    components: scoring.ComponentScores
    score: scoring.ScoreResult
    status: str  # accepted | provisional | rejected | duplicate
    reason: str | None
    clean_boundaries: bool
    transcript_excerpt: str
    signals: dict[str, Any]
    latency_ms: int


def evaluate(tl: Timeline, peak: float, *, analyzer: ContextAnalyzer, weights: dict[str, float],
             min_quality: float, min_confidence: float, min_clip_s: float, max_clip_s: float,
             accepted_so_far: int, target_max: int, existing: list[tuple[float, float, str]],
             stream_title: str | None = None, game: str | None = None, lo: float | None = None,
             hi: float | None = None) -> MomentResult:
    t_start = time.monotonic()
    tl.finalize()
    lo = tl.t0 if lo is None else lo
    hi = tl.t0 + tl.span() if hi is None else hi
    sig = signal_summary(tl, peak)
    window = ContextWindow(words=[w for w in tl.words if peak - 60 <= w.start <= peak + 20], peak=peak,
                           window_start=max(lo, peak - 60), window_end=min(hi, peak + 20), signals=sig,
                           stream_title=stream_title, game=game)
    verdict = analyzer.analyze(window)
    start, end, clean = snap_boundaries(tl.words, peak, verdict.suggested_start, verdict.suggested_end,
                                        min_s=min_clip_s, max_s=max_clip_s, lo=lo, hi=hi)
    comps = component_scores(tl, peak, verdict)
    res = scoring.combine(comps, weights, analyzer_confidence=verdict.confidence)
    excerpt = "".join(w.w for w in tl.words if start <= w.start <= end).strip()

    status, reason = "accepted", None
    if not verdict.is_highlight:
        status, reason = "rejected", "context analysis: not a self-contained highlight"
    else:
        g = scoring.gate(res, min_quality=min_quality, min_confidence=min_confidence,
                         accepted_so_far=accepted_so_far, target_max=target_max)
        if not g.accepted:
            status, reason = ("provisional" if g.provisional else "rejected"), g.reason
    if status in ("accepted", "provisional"):
        dup = scoring.is_duplicate((start, end), excerpt, existing)
        if dup:
            status, reason = "duplicate", dup
    if status == "accepted" and tl.words and not clean:
        # Without clean sentence boundaries the clip would start/end awkwardly.
        res.reasons.append("boundaries not on sentence edges")
        if res.quality < min_quality + 0.1:
            status, reason = "rejected", "could not find clean sentence boundaries"
    return MomentResult(peak, start, end, verdict, comps, res, status, reason, clean, excerpt, sig,
                        int((time.monotonic() - t_start) * 1000))
