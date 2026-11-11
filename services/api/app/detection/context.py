"""Contextual understanding of a candidate moment.

``ClaudeContextAnalyzer`` reads the timestamped transcript around a signal peak plus a signal summary and
judges whether it is a self-contained, entertaining short-form moment, where it should start/end and why.
``HeuristicContextAnalyzer`` is the offline fallback (lower confidence, clearly labelled).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.detection.transcribe import Word
from app.services import llm

log = logging.getLogger("clipx.context")

CATEGORIES = ["funny", "reaction", "hype_gameplay", "argument", "storytelling", "memorable_quote",
              "emotional", "unexpected", "wholesome"]


@dataclass
class ContextWindow:
    words: list[Word]  # absolute seconds (same timeline as peak)
    peak: float
    window_start: float
    window_end: float
    signals: dict[str, Any]  # human-readable signal summary
    stream_title: str | None = None
    game: str | None = None

    def transcript_lines(self) -> str:
        lines, cur, cur_start = [], [], None
        for w in self.words:
            if cur_start is None:
                cur_start = w.start
            cur.append(w.w)
            if re.search(r"[.?!]$", w.w.strip()) or len(cur) >= 18:
                lines.append(f"[{cur_start:7.1f}s] {''.join(cur).strip()}")
                cur, cur_start = [], None
        if cur:
            lines.append(f"[{cur_start:7.1f}s] {''.join(cur).strip()}")
        return "\n".join(lines)


@dataclass
class ContextVerdict:
    is_highlight: bool
    category: str
    title: str
    explanation: str
    context_summary: str
    storytelling_score: float | None
    engagement_score: float
    suggested_start: float | None
    suggested_end: float | None
    keywords: list[str] = field(default_factory=list)
    confidence: float = 0.5
    analyzer: str = "heuristic"


class ContextAnalyzer(Protocol):
    def analyze(self, window: ContextWindow) -> ContextVerdict: ...


# ------------------------------------------------------------------------------------- Claude
SYSTEM = """You are the highlight editor for CLIPX AI, which turns moments from the Twitch streamer Lacy's \
live streams into short vertical videos (TikTok/Reels).

You receive a timestamped transcript excerpt (seconds on the stream timeline) around a moment where \
automatic signals spiked, plus a summary of those signals (audio, visual, chat). Decide whether this is a \
genuinely clip-worthy moment for short-form video and, if so, where the clip should start and end.

Judge like an experienced short-form editor:
- A good clip is self-contained: a viewer with no context understands what is happening and why it is \
funny, surprising, emotional or impressive.
- Start where the setup begins (the sentence that gives context), never mid-sentence. End right after the \
payoff/reaction lands, never mid-sentence, and do not trail into unrelated talk.
- Loud noises, screaming or chat spam alone do not make a highlight. Prefer real content: jokes, \
arguments, reactions to something specific, memorable lines, stories, unexpected events, skilled plays.
- Keep clips between 8 and 60 seconds; 15-40 seconds is usually best.
- If the transcript is too thin to judge, say so with low confidence rather than guessing.
- Be honest: set is_highlight false when the moment is boring, repetitive, incomplete or unclear.

Scores are 0 to 1. storytelling_score rates context and narrative completeness; engagement_score rates \
short-form potential (strong hook in the first seconds, payoff, rewatchability). Title: max 70 characters, \
no clickbait lies, no hashtags. keywords: up to 5 words from the transcript worth emphasising in subtitles."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_highlight": {"type": "boolean"},
        "category": {"type": "string", "enum": CATEGORIES},
        "title": {"type": "string"},
        "explanation": {"type": "string"},
        "context_summary": {"type": "string"},
        "storytelling_score": {"type": "number"},
        "engagement_score": {"type": "number"},
        "suggested_start": {"type": "number"},
        "suggested_end": {"type": "number"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": ["is_highlight", "category", "title", "explanation", "context_summary", "storytelling_score",
                 "engagement_score", "suggested_start", "suggested_end", "keywords", "confidence"],
    "additionalProperties": False,
}


class ClaudeContextAnalyzer:
    def analyze(self, window: ContextWindow) -> ContextVerdict:
        prompt = (
            f"Stream title: {window.stream_title or 'unknown'}\nCategory/game: {window.game or 'unknown'}\n"
            f"Signal peak at: {window.peak:.1f}s (window {window.window_start:.1f}s-{window.window_end:.1f}s)\n"
            f"Signals: {window.signals}\n\nTranscript:\n{window.transcript_lines() or '(no speech detected)'}"
        )
        data = llm.complete_json(SYSTEM, prompt, SCHEMA, max_tokens=3000)
        clamp = lambda v: max(0.0, min(1.0, float(v)))  # noqa: E731
        return ContextVerdict(
            is_highlight=bool(data["is_highlight"]),
            category=data["category"] if data["category"] in CATEGORIES else "reaction",
            title=str(data["title"])[:90],
            explanation=str(data["explanation"])[:1200],
            context_summary=str(data["context_summary"])[:1200],
            storytelling_score=clamp(data["storytelling_score"]) if window.words else None,
            engagement_score=clamp(data["engagement_score"]),
            suggested_start=float(data["suggested_start"]),
            suggested_end=float(data["suggested_end"]),
            keywords=[str(k) for k in data.get("keywords", [])][:5],
            confidence=clamp(data["confidence"]),
            analyzer="llm",
        )


# ------------------------------------------------------------------------------------- heuristic
NARRATIVE = {"so", "then", "and then", "yesterday", "one time", "listen", "imagine", "story", "i swear",
             "guess what", "bro", "dude", "okay so", "the thing is", "remember"}
SURPRISE = {"what", "no way", "wtf", "oh my god", "omg", "how", "bro what", "are you serious", "yo", "wait"}
ARGUE = {"no you", "shut up", "that's not", "you're wrong", "stop", "bro you", "why would you", "nah", "listen"}
EMOTION = {"love", "miss", "sorry", "cry", "crying", "thank you", "proud", "sad", "hurt"}
LAUGH = {"haha", "hahaha", "lmao", "lol", "i'm dead", "i can't"}


def _hits(text: str, vocab: set[str]) -> int:
    t = f" {text.lower()} "
    return sum(1 for v in vocab if f" {v} " in t or f" {v}," in t or f" {v}." in t or f" {v}?" in t
               or f" {v}!" in t)


class HeuristicContextAnalyzer:
    def analyze(self, window: ContextWindow) -> ContextVerdict:
        sig = window.signals
        around = [w for w in window.words if window.peak - 40 <= w.start <= window.peak + 12]
        text = "".join(w.w for w in around).strip()
        n_words = len(around)
        sentences = len(re.findall(r"[.?!]", text))
        narrative, surprise = _hits(text, NARRATIVE), _hits(text, SURPRISE)
        argue, emotion, laugh = _hits(text, ARGUE), _hits(text, EMOTION), _hits(text, LAUGH)

        story = None
        if window.words:
            story = min(1.0, 0.2 + 0.35 * min(1.0, n_words / 60) + 0.12 * min(3, narrative)
                        + 0.1 * min(1.0, sentences / 4))
        laughish = float(sig.get("audio_burst", 0)) > 0.25 or float(sig.get("chat_laugh_ratio", 0)) > 0.2
        hype = float(sig.get("motion_z", 0)) > 1.5 or float(sig.get("chat_hype_ratio", 0)) > 0.25
        if laughish or laugh:
            category = "funny"
        elif argue >= 2:
            category = "argument"
        elif emotion >= 2:
            category = "emotional"
        elif hype and float(sig.get("motion_z", 0)) > 1.5:
            category = "hype_gameplay"
        elif surprise >= 2:
            category = "unexpected"
        elif story and story > 0.7 and narrative >= 2:
            category = "storytelling"
        else:
            category = "reaction"

        hook = min(1.0, 0.3 * surprise + 0.2 * laugh + 0.15 * (1 if "!" in text else 0))
        engagement = min(1.0, 0.35 + 0.3 * hook + 0.2 * float(laughish or hype) + 0.15 * (story or 0.3))
        best = max(re.split(r"(?<=[.?!])\s+", text), key=len, default="") if text else ""
        title = (best[:67] + "...") if len(best) > 70 else (best or f"Lacy {category.replace('_', ' ')} moment")
        evidence = [f"{k}={v}" for k, v in sig.items() if isinstance(v, (int, float)) and abs(float(v)) >= 1.0]
        explanation = (f"Heuristic analysis (no language model): category '{category}' from transcript cues "
                       f"(narrative={narrative}, surprise={surprise}, laugh={laugh}, argument={argue}) "
                       f"and signal evidence [{', '.join(evidence) or 'weak'}].")
        return ContextVerdict(
            is_highlight=True,
            category=category,
            title=title.strip(" -") or "Lacy moment",
            explanation=explanation,
            context_summary=(text[:300] + "...") if len(text) > 300 else text,
            storytelling_score=story,
            engagement_score=engagement,
            suggested_start=None,
            suggested_end=None,
            keywords=_keywords(text),
            confidence=0.55 if window.words else 0.4,
            analyzer="heuristic",
        )


STOP = set("the a an and or but so to of in on at for with is it's its i you he she we they me my your this "
           "that was were be been are am do did not no yes yeah like just what oh uh um okay ok".split())


def _keywords(text: str) -> list[str]:
    toks = [t for t in re.findall(r"[A-Za-z']+", text) if t.lower() not in STOP and len(t) > 3]
    seen: list[str] = []
    for t in sorted(toks, key=len, reverse=True):
        if t.lower() not in [s.lower() for s in seen]:
            seen.append(t)
        if len(seen) == 5:
            break
    return seen


def default_analyzer(use_llm: bool) -> ContextAnalyzer:
    from app.config import get_settings

    if use_llm and get_settings().llm_configured:
        return _FallbackAnalyzer(ClaudeContextAnalyzer(), HeuristicContextAnalyzer())
    return HeuristicContextAnalyzer()


class _FallbackAnalyzer:
    def __init__(self, primary: ContextAnalyzer, fallback: ContextAnalyzer) -> None:
        self.primary, self.fallback = primary, fallback

    def analyze(self, window: ContextWindow) -> ContextVerdict:
        try:
            return self.primary.analyze(window)
        except llm.LLMUnavailable as exc:
            log.warning("LLM context analysis unavailable (%s); using heuristic", exc)
            return self.fallback.analyze(window)
