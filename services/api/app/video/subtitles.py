"""Subtitle cues and ASS rendering.

Style: Netflix-inspired bold sans, bright yellow, heavy dark outline + soft shadow, mobile-safe margins,
short 1-line cues (2-4 words) that follow speech exactly from word timestamps. Selected important words
are emphasised (white + quick scale pop). Cues are moved to a different safe zone whenever they would
cover a face in the output frame.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

OUT_W, OUT_H = 1080, 1920

DEFAULT_STYLE: dict[str, Any] = {
    "font": "Inter ExtraBold",
    "size": 90,
    "primary": "#FFE11A",  # bright yellow
    "highlight": "#FFFFFF",
    "outline": 7,
    "shadow": 3,
    "max_chars": 20,
    "max_words": 4,
    "uppercase": False,
    "emphasis": True,
}

# Safe zones (vertical centre, px) avoiding TikTok UI: bottom caption bar and right-side buttons.
ZONES = {"lower": 1360, "middle": 1080, "upper": 520}


@dataclass
class Cue:
    start: float
    end: float
    words: list[dict[str, Any]]  # [{w, start, end, emph}]
    zone: str = "lower"

    @property
    def text(self) -> str:
        return " ".join(w["w"] for w in self.words)

    def as_dict(self) -> dict[str, Any]:
        return {"start": round(self.start, 3), "end": round(self.end, 3), "zone": self.zone,
                "words": [{"w": w["w"], "start": round(w["start"], 3), "end": round(w["end"], 3),
                           "emph": bool(w.get("emph"))} for w in self.words]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Cue:
        return cls(float(d["start"]), float(d["end"]), list(d["words"]), d.get("zone", "lower"))


STOP = set("the a an and or but so to of in on at for with is it it's its i i'm you he she we they me my "
           "your this that was were be been are am do did not just like yeah uh um oh okay ok what".split())


def pick_emphasis(words: list[dict[str, Any]], keywords: list[str], *, max_ratio: float = 0.12) -> set[int]:
    """Indices of words to emphasise: model keywords, exclamations, numbers, ALL-CAPS — sparingly."""
    kw = {k.lower().strip(".,!?") for k in keywords}
    scored = []
    for i, w in enumerate(words):
        raw = w["w"].strip()
        bare = raw.lower().strip(".,!?\"'")
        if not bare or bare in STOP:
            continue
        s = 0.0
        if bare in kw:
            s += 3
        if raw.endswith("!"):
            s += 1.5
        if re.fullmatch(r"\d+[%$k]?", bare):
            s += 1.2
        if raw.isupper() and len(raw) > 2:
            s += 1.0
        if len(bare) >= 7:
            s += 0.4
        if s > 0:
            scored.append((s, i))
    budget = max(1, int(len(words) * max_ratio))
    chosen: set[int] = set()
    for _, i in sorted(scored, reverse=True):
        if len(chosen) >= budget:
            break
        if any(abs(i - j) < 4 for j in chosen):  # never two emphasised words close together
            continue
        chosen.add(i)
    return chosen


def build_cues(words: list[dict[str, Any]], *, style: dict[str, Any] | None = None,
               keywords: list[str] | None = None, clip_start: float = 0.0, clip_end: float | None = None
               ) -> list[Cue]:
    """Group word timestamps (seconds relative to the clip) into short readable cues."""
    st = {**DEFAULT_STYLE, **(style or {})}
    ws = [dict(w) for w in words if w["w"].strip() and w["end"] > clip_start
          and (clip_end is None or w["start"] < clip_end)]
    for w in ws:
        w["w"] = w["w"].strip()
    emph = pick_emphasis(ws, keywords or []) if st["emphasis"] else set()
    for i, w in enumerate(ws):
        w["emph"] = i in emph
    cues: list[Cue] = []
    cur: list[dict[str, Any]] = []

    def flush() -> None:
        if cur:
            cues.append(Cue(cur[0]["start"], cur[-1]["end"], list(cur)))
            cur.clear()

    for w in ws:
        if cur:
            gap = w["start"] - cur[-1]["end"]
            length = len(" ".join(x["w"] for x in cur + [w]))
            if gap > 0.6 or length > st["max_chars"] or len(cur) >= st["max_words"]:
                flush()
        cur.append(w)
        if re.search(r"[.?!,;:]$", w["w"]) and (len(cur) >= 2 or w["w"][-1] in ".?!"):
            flush()
    flush()
    # Timing: never overlap, minimum on-screen time for readability, bridge small gaps.
    for i, c in enumerate(cues):
        nxt = cues[i + 1].start if i + 1 < len(cues) else None
        c.end = max(c.end, c.start + 0.45)
        if nxt is not None:
            if nxt - c.end < 0.25:
                c.end = nxt
            c.end = min(c.end, nxt)
        if clip_end is not None:
            c.end = min(c.end, clip_end)
    return [c for c in cues if c.end > c.start]


def place_cues(cues: list[Cue], face_boxes_out: list[tuple[float, float, float, float, float]]) -> None:
    """Choose a zone per cue so text never covers a face. face boxes: (t, x, y, w, h) in output px."""
    half = 95  # approx half text-block height incl. outline
    for c in cues:
        faces = [f for f in face_boxes_out if c.start - 0.3 <= f[0] <= c.end + 0.3]
        for zone in ("lower", "middle", "upper"):
            y = ZONES[zone]
            if not any(fy - 20 < y + half and y - half < fy + fh + 20 for _, _, fy, _, fh in faces):
                c.zone = zone
                break
        else:
            c.zone = "lower"


def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def to_ass(cues: list[Cue], style: dict[str, Any] | None = None) -> str:
    st = {**DEFAULT_STYLE, **(style or {})}
    prim, hi = _ass_color(st["primary"]), _ass_color(st["highlight"])
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {OUT_W}
PlayResY: {OUT_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, \
Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, \
MarginV, Encoding
Style: Cap,{st['font']},{st['size']},{prim},{prim},&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,{st['outline']},\
{st['shadow']},5,80,80,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for c in cues:
        y = ZONES.get(c.zone, ZONES["lower"])
        parts = []
        for w in c.words:
            txt = _escape(w["w"].upper() if st["uppercase"] else w["w"])
            if w.get("emph"):
                # emphasised word: highlight colour + quick pop when it is spoken
                t_on = int(max(0.0, w["start"] - c.start) * 1000)
                parts.append(
                    f"{{\\c{hi}\\fscx100\\fscy100\\t({t_on},{t_on + 90},\\fscx116\\fscy116)"
                    f"\\t({t_on + 90},{t_on + 200},\\fscx108\\fscy108)}}{txt}{{\\c{prim}\\fscx100\\fscy100}}")
            else:
                parts.append(txt)
        body = " ".join(parts)
        pop = "\\fad(40,0)\\fscx92\\fscy92\\t(0,90,\\fscx100\\fscy100)"
        lines.append(f"Dialogue: 0,{_ts(c.start)},{_ts(c.end)},Cap,,0,0,0,,{{\\an5\\pos({OUT_W // 2},{y}){pop}}}{body}")
    return header + "\n".join(lines) + "\n"


def to_srt(cues: list[Cue]) -> str:
    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    return "\n".join(f"{i}\n{ts(c.start)} --> {ts(c.end)}\n{c.text}\n" for i, c in enumerate(cues, 1))
