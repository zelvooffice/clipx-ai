"""Short-form captions + hashtags.

Rules: mention Lacy naturally, always include #Lacy, 3-6 relevant hashtags (no spam), no misleading claims,
a hook in the first words, unique per clip (checked against recent captions).
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass
from typing import Any

from app.detection.scoring import text_similarity
from app.services import llm

CATEGORY_TAGS = {
    "funny": ["#funny", "#streamerclips"], "reaction": ["#reaction", "#streamerclips"],
    "hype_gameplay": ["#gaming", "#clutch"], "argument": ["#debate", "#streamerclips"],
    "storytelling": ["#storytime"], "memorable_quote": ["#quotes", "#streamerclips"],
    "emotional": ["#wholesome"], "unexpected": ["#unexpected", "#wtf"], "wholesome": ["#wholesome"],
}
BASE_TAGS = ["#Lacy", "#twitch", "#twitchclips"]

HOOKS = {
    "funny": ["Lacy really said this with a straight face 😭", "I can't stop replaying this Lacy moment 😂",
              "Lacy was NOT ready for this 💀", "Nobody warned Lacy about this one 😂"],
    "reaction": ["Lacy's reaction says everything 😳", "Watch Lacy's face at the end 👀",
                 "Lacy did not see that coming 😳"],
    "hype_gameplay": ["Lacy went crazy here 🔥", "This play from Lacy is unreal 🎯", "Lacy locked in 🔒🔥"],
    "argument": ["Lacy was not backing down 😤", "Who's right here, Lacy or chat? 🤔"],
    "storytelling": ["Lacy's story got crazier every second 👀", "Wait for how this Lacy story ends 😭"],
    "memorable_quote": ["Lacy said what we were all thinking 💯", "Lacy with the quote of the day 💬"],
    "emotional": ["A real moment from Lacy 🤍", "Lacy got honest here 🤍"],
    "unexpected": ["Nobody expected this on Lacy's stream 😳", "This is why you watch Lacy live 😳"],
    "wholesome": ["Lacy being wholesome for a second 🤍"],
}

SYSTEM = """You write TikTok captions for short clips from the Twitch streamer Lacy's live streams.
Rules:
- 1-2 short sentences, max 150 characters before hashtags. Start with a hook. Mention Lacy naturally.
- Describe only what actually happens in the transcript/summary. No invented events, no fake quotes, \
no clickbait lies, no "part 2" unless it exists.
- 3 to 6 hashtags total, always including #Lacy. Only hashtags relevant to this clip's content \
(game name, topic, category). No generic spam like #fyp #viral #foryou.
- Avoid repeating the wording of the recent captions you are given."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"caption": {"type": "string"}, "hashtags": {"type": "array", "items": {"type": "string"}}},
    "required": ["caption", "hashtags"],
    "additionalProperties": False,
}


@dataclass
class CaptionResult:
    caption: str
    hashtags: list[str]
    source: str  # llm | template

    @property
    def full_text(self) -> str:
        return f"{self.caption} {' '.join(self.hashtags)}".strip()


def normalize_tags(tags: list[str], *, game: str | None) -> list[str]:
    out: list[str] = []
    for t in tags:
        t = "#" + re.sub(r"[^A-Za-z0-9_]", "", t.lstrip("#"))
        if len(t) > 1 and t.lower() not in {x.lower() for x in out} and t.lower() not in {"#fyp", "#foryou",
                                                                                         "#viral", "#foryoupage"}:
            out.append(t)
    if "#lacy" not in {x.lower() for x in out}:
        out.insert(0, "#Lacy")
    else:
        out = ["#Lacy"] + [x for x in out if x.lower() != "#lacy"]
    if game:
        gt = "#" + re.sub(r"[^A-Za-z0-9]", "", game)
        if 2 < len(gt) <= 25 and gt.lower() not in {x.lower() for x in out} and len(out) < 6:
            out.append(gt)
    return out[:6]


def generate(*, clip_id: str, title: str, category: str, transcript: str, context: str | None,
             game: str | None, recent: list[str], use_llm: bool = True) -> CaptionResult:
    if use_llm:
        try:
            prompt = (f"Clip title: {title}\nCategory: {category}\nGame/category on stream: {game or 'unknown'}\n"
                      f"Context: {context or 'n/a'}\nTranscript: {transcript[:1500] or '(no speech)'}\n\n"
                      f"Recent captions (do not repeat):\n" + "\n".join(f"- {r}" for r in recent[:15]))
            data = llm.complete_json(SYSTEM, prompt, SCHEMA, max_tokens=1500)
            cap = str(data["caption"]).strip()[:220]
            if "lacy" not in cap.lower():
                cap = f"Lacy: {cap}"
            res = CaptionResult(cap, normalize_tags(list(data["hashtags"]), game=game), "llm")
            if not _too_similar(res.caption, recent):
                return res
        except llm.LLMUnavailable:
            pass
    return template_caption(clip_id=clip_id, title=title, category=category, transcript=transcript, game=game,
                            recent=recent)


def template_caption(*, clip_id: str, title: str, category: str, transcript: str, game: str | None,
                     recent: list[str]) -> CaptionResult:
    rng = random.Random(int(hashlib.sha256(clip_id.encode()).hexdigest()[:8], 16))
    hooks = list(HOOKS.get(category, HOOKS["reaction"]))
    rng.shuffle(hooks)
    quote = _best_quote(transcript)
    for hook in hooks:
        cap = f'{hook} "{quote}"' if quote and rng.random() < 0.6 else hook
        if not _too_similar(cap, recent):
            break
    else:
        cap = f"{hooks[0]} — {title[:60]}"
    tags = BASE_TAGS + CATEGORY_TAGS.get(category, [])
    return CaptionResult(cap, normalize_tags(tags, game=game), "template")


def _best_quote(transcript: str) -> str | None:
    sents = [s.strip() for s in re.split(r"(?<=[.?!])\s+", transcript) if 12 <= len(s.strip()) <= 70]
    if not sents:
        return None
    return max(sents, key=lambda s: (s.count("!") + s.count("?"), -abs(len(s) - 40)))


def _too_similar(cap: str, recent: list[str]) -> bool:
    c = cap.lower().strip()
    return any(c == r.lower().strip() or text_similarity(c, r) > 0.5 for r in recent)
