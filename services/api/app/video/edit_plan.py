"""Content-aware edit decisions: punch-ins, push-ins, sound design, fades.

Decisions come from the clip's category, emphasised words and audio peaks, and are seeded by the clip id —
so two clips never receive an identical edit, and re-rendering the same clip is deterministic.
"""

from __future__ import annotations

import hashlib
import random
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

# How energetic the edit is per category (0 = static, 1 = punchy).
INTENSITY = {"funny": 1.0, "hype_gameplay": 0.9, "unexpected": 0.85, "reaction": 0.8, "argument": 0.6,
             "memorable_quote": 0.5, "wholesome": 0.4, "storytelling": 0.35, "emotional": 0.25}
SFX_CATEGORIES = {"funny", "hype_gameplay", "unexpected", "reaction"}


@dataclass
class Zoom:
    start: float
    end: float
    factor: float
    ease_in: float
    ease_out: float
    reason: str


@dataclass
class Sfx:
    t: float
    kind: str  # whoosh | pop
    gain_db: float


@dataclass
class EditPlan:
    style: str
    intensity: float
    push_in: float  # total slow zoom across the clip (1.0 = none)
    zooms: list[Zoom] = field(default_factory=list)
    sfx: list[Sfx] = field(default_factory=list)
    fade_in: float = 0.12
    fade_out: float = 0.35
    notes: list[str] = field(default_factory=list)

    def zoom_at(self, t: float, duration: float) -> float:
        z = 1.0 + (self.push_in - 1.0) * (t / max(duration, 1e-3))
        for zm in self.zooms:
            if zm.start <= t <= zm.end:
                if zm.ease_in and t < zm.start + zm.ease_in:
                    k = (t - zm.start) / zm.ease_in
                elif zm.ease_out and t > zm.end - zm.ease_out:
                    k = (zm.end - t) / zm.ease_out
                else:
                    k = 1.0
                k = k * k * (3 - 2 * k)  # smoothstep
                z *= 1.0 + (zm.factor - 1.0) * k
        return z

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def seed_for(clip_id: str, version: int = 1) -> int:
    return int(hashlib.sha256(f"{clip_id}:{version}".encode()).hexdigest()[:12], 16)


def build_plan(*, clip_id: str, category: str, duration: float, cues: list[dict[str, Any]],
               audio_peaks: list[float], zoom_intensity: float = 1.0, sfx_enabled: bool = True,
               version: int = 1) -> EditPlan:
    rng = random.Random(seed_for(clip_id, version))
    base = INTENSITY.get(category, 0.6)
    intensity = max(0.0, min(1.2, base * zoom_intensity))
    style = rng.choice(["snap", "glide"]) if intensity >= 0.75 else "glide"
    plan = EditPlan(style=style, intensity=round(intensity, 2), push_in=1.0)
    plan.notes.append(f"category '{category}' -> intensity {intensity:.2f}, style {style}")

    if intensity == 0:
        plan.notes.append("punch-ins disabled")
        return plan
    if category in ("storytelling", "emotional", "wholesome", "memorable_quote"):
        plan.push_in = 1.0 + rng.uniform(0.04, 0.08) * min(1.0, intensity * 2)
        plan.notes.append(f"slow push-in to {plan.push_in:.2f}x for a calmer, story-led edit")

    # Moments worth emphasising: emphasised subtitle words first, then audio peaks.
    emph_times = [w["start"] for c in cues for w in c.get("words", []) if w.get("emph")]
    candidates = [(t, "emphasised word") for t in emph_times] + [(t, "audio peak") for t in audio_peaks]
    candidates.sort(key=lambda x: x[0])
    budget = max(0, int(round(duration / 8.0 * intensity)))
    min_gap = 3.5 if intensity > 0.7 else 5.5
    chosen: list[tuple[float, str]] = []
    for t, why in candidates:
        if t < 0.8 or t > duration - 1.2:
            continue
        if all(abs(t - c[0]) >= min_gap for c in chosen):
            chosen.append((t, why))
        if len(chosen) >= budget:
            break
    for t, why in chosen:
        factor = 1.0 + rng.uniform(0.08, 0.2) * intensity
        hold = rng.uniform(0.7, 1.6)
        snap = style == "snap" and rng.random() < 0.6
        plan.zooms.append(Zoom(round(t - 0.05, 3), round(min(duration, t + hold), 3), round(factor, 3),
                               0.0 if snap else 0.14, 0.22, why))
        if sfx_enabled and category in SFX_CATEGORIES and rng.random() < 0.75:
            plan.sfx.append(Sfx(round(max(0.0, t - (0.08 if snap else 0.18)), 3),
                                "pop" if snap else "whoosh", -20.0 - rng.uniform(0, 4)))
    plan.notes.append(f"{len(plan.zooms)} punch-ins, {len(plan.sfx)} sound accents")
    return plan


# ------------------------------------------------------------------------------- synthesized SFX
def write_sfx(kind: str, path: Path, sr: int = 48000) -> Path:
    """Generate subtle original sound accents (no third-party assets)."""
    rng = np.random.default_rng(7 if kind == "whoosh" else 11)
    if kind == "whoosh":
        n = int(0.35 * sr)
        noise = rng.standard_normal(n)
        t = np.linspace(0, 1, n)
        # sweep a resonant band-pass upward by mixing progressively brighter filtered noise
        out = np.zeros(n)
        for lo, weight in ((200, 1.0), (800, 0.7), (2400, 0.45)):
            k = max(3, int(sr / lo / 2))
            smoothed = np.convolve(noise, np.ones(k) / k, mode="same")
            out += weight * smoothed * (t ** (1 + lo / 1200))
        env = np.sin(np.pi * t) ** 2
        sig = out * env
    else:  # pop
        n = int(0.12 * sr)
        t = np.arange(n) / sr
        freq = 900 * np.exp(-t * 25) + 220
        sig = np.sin(2 * np.pi * np.cumsum(freq) / sr) * np.exp(-t * 38)
    sig = sig / (np.max(np.abs(sig)) + 1e-9) * 0.8
    pcm = (sig * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path
