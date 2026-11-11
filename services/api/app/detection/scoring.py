"""Highlight scoring (docs/ARCHITECTURE.md §4.4).

Components (0..1, ``None`` when the signal is unavailable):
  storytelling_context (30%), visual_interest (25%), audio_speech (15%),
  audience_reaction (15%), engagement_potential (15%).
Unavailable components are excluded and the remaining weights renormalised; confidence then drops with
the missing coverage. A moment is only accepted when at least two *independent signal families* support
it, so loudness alone (audio) or chat alone can never produce a clip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

COMPONENTS = ("storytelling_context", "visual_interest", "audio_speech", "audience_reaction",
              "engagement_potential")

# Which raw evidence family backs each component (engagement_potential is derived, not independent).
FAMILY_OF = {"storytelling_context": "speech", "visual_interest": "visual", "audio_speech": "audio",
             "audience_reaction": "chat"}


@dataclass
class ComponentScores:
    storytelling_context: float | None = None
    visual_interest: float | None = None
    audio_speech: float | None = None
    audience_reaction: float | None = None
    engagement_potential: float | None = None

    def as_dict(self) -> dict[str, float | None]:
        return {k: (None if getattr(self, k) is None else round(float(getattr(self, k)), 3)) for k in COMPONENTS}


@dataclass
class ScoreResult:
    quality: float
    confidence: float
    coverage: float
    supporting_families: list[str]
    available: list[str]
    reasons: list[str] = field(default_factory=list)


def combine(scores: ComponentScores, weights: dict[str, float], *, analyzer_confidence: float = 0.6,
            support_threshold: float = 0.55) -> ScoreResult:
    avail = [k for k in COMPONENTS if getattr(scores, k) is not None]
    if not avail:
        return ScoreResult(0.0, 0.0, 0.0, [], [], ["no signals available"])
    total_w = sum(weights[k] for k in COMPONENTS)
    avail_w = sum(weights[k] for k in avail)
    quality = sum(weights[k] * float(getattr(scores, k)) for k in avail) / max(avail_w, 1e-9)
    coverage = avail_w / max(total_w, 1e-9)
    values = np.array([float(getattr(scores, k)) for k in avail])
    agreement = 1.0 - min(0.5, float(values.std())) if len(values) > 1 else 0.7
    confidence = (coverage**0.7) * agreement * max(0.0, min(1.0, analyzer_confidence))
    families = sorted({FAMILY_OF[k] for k in avail if k in FAMILY_OF
                       and float(getattr(scores, k)) >= support_threshold})
    return ScoreResult(round(quality, 4), round(confidence, 4), round(coverage, 4), families, avail)


@dataclass
class Gate:
    accepted: bool
    provisional: bool
    reason: str | None


def gate(result: ScoreResult, *, min_quality: float, min_confidence: float, accepted_so_far: int,
         target_max: int) -> Gate:
    """Acceptance rules. The effective threshold only ever *rises* as the clip count grows."""
    effective_min = min_quality
    if target_max > 0 and accepted_so_far >= int(target_max * 0.8):
        effective_min = min_quality + 0.06 * (accepted_so_far - int(target_max * 0.8) + 1)
    if accepted_so_far >= target_max:
        return Gate(False, False, f"per-stream cap of {target_max} clips reached")
    if result.quality < effective_min:
        return Gate(False, False, f"quality {result.quality:.2f} below threshold {effective_min:.2f}")
    if len(result.supporting_families) < 2:
        fams = ", ".join(result.supporting_families) or "none"
        if result.supporting_families == ["chat"]:
            # Chat can nominate a moment but never accept it on its own: needs media verification.
            return Gate(False, True, "only the chat signal supports this moment; awaiting media verification")
        return Gate(False, False, f"needs >= 2 independent signal families (supported by: {fams})")
    if result.confidence < min_confidence:
        return Gate(False, False, f"confidence {result.confidence:.2f} below {min_confidence:.2f}")
    return Gate(True, False, None)


# ----------------------------------------------------------------------------------------- dedupe
def iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


def _shingles(text: str, n: int = 3) -> set[str]:
    toks = re.findall(r"[a-z0-9']+", text.lower())
    return {" ".join(toks[i:i + n]) for i in range(max(0, len(toks) - n + 1))}


def text_similarity(a: str, b: str) -> float:
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def is_duplicate(new: tuple[float, float], new_text: str,
                 existing: list[tuple[float, float, str]]) -> str | None:
    for s, e, t in existing:
        if iou(new, (s, e)) > 0.35:
            return f"overlaps an existing clip ({s:.0f}-{e:.0f}s)"
        if text_similarity(new_text, t) > 0.6:
            return "repeats the same statement as an existing clip"
    return None


def dedupe_key(peak_offset_s: float) -> str:
    return f"peak:{int(peak_offset_s // 8)}"
