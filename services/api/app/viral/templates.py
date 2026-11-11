"""Viral Edits template engine + trend-based format suggestions.

Edits are composed ONLY from CLIPX's own rendered clips whose media came from an authorized source
(the channel's live feed or Twitch Get Clips Download). Popular third-party videos are used as
*format inspiration* (titles/metrics), never as media.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AnalyticsSnapshot,
    Clip,
    ClipOrigin,
    EditStatus,
    MediaSource,
    Verification,
    ViralEdit,
    ViralSourceVideo,
)
from app.services import jobs
from app.services.storage import get_storage
from app.video.subtitles import _ass_color, _escape

W, H = 1080, 1920
AUTHORIZED_MEDIA = {MediaSource.live_buffer, MediaSource.twitch_download, MediaSource.upload}


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    description: str
    min_clips: int
    max_clips: int
    text_hint: str
    title_patterns: tuple[str, ...]  # how this format shows up in popular titles


TEMPLATES: dict[str, Template] = {t.key: t for t in [
    Template("how_me_and_bro_look", "How me and bro look", "Two moments stacked top/bottom with a shared caption.",
             2, 2, "when the plan actually works", (r"\bme and (my )?bro\b", r"\bhow (we|me and)\b")),
    Template("pov", "POV", "One clip with a POV banner that frames the moment from the viewer's side.",
             1, 1, "you said something you shouldn't have", (r"\bpov\b",)),
    Template("compilation", "Compilation", "3-5 short best moments of one kind, numbered.", 3, 5,
             "Lacy's funniest moments this week", (r"\bcompilation\b", r"\bbest (of|moments)\b", r"\btop \d+\b")),
    Template("reaction", "Reaction", "Main moment with the reaction moment picture-in-picture.", 2, 2,
             "Lacy reacting to", (r"\breact(s|ion|ing)?\b",)),
    Template("before_after", "Before / After", "Two moments stacked with BEFORE and AFTER labels.", 2, 2,
             "", (r"\bbefore\b.*\bafter\b",)),
    Template("storytime", "Storytime", "A storytelling clip with a STORYTIME header.", 1, 1,
             "the craziest thing happened", (r"\bstory(time)?\b",)),
]}


# ------------------------------------------------------------------------------------ suggestions
def suggest(db: Session) -> list[dict[str, Any]]:
    sources = db.scalars(select(ViralSourceVideo).order_by(ViralSourceVideo.popularity_score.desc()).limit(50)).all()
    usable = _usable_clips(db)
    by_cat = Counter(c.category for c in usable)
    perf = _template_performance(db)
    out = []
    for t in TEMPLATES.values():
        hits = [s for s in sources if s.title and any(re.search(p, s.title, re.I) for p in t.title_patterns)]
        evidence = []
        score = 0.0
        if hits:
            evidence.append(f"{len(hits)} of the top {len(sources)} popular Lacy videos use this format "
                            f"(e.g. \"{(hits[0].title or '')[:60]}\")")
            score += len(hits) / max(1, len(sources)) * 3
        if t.key in perf:
            p = perf[t.key]
            evidence.append(f"your {p['posts']} published {t.name} edits average {p['avg_views']:,} views")
            score += 1.0
        fit = _fitting_clips(t, usable, by_cat)
        if len(fit) < t.min_clips:
            evidence.append(f"needs {t.min_clips} suitable clips with authorized media (have {len(fit)})")
            score -= 1
        out.append({"template": t.key, "name": t.name, "description": t.description, "score": round(score, 3),
                    "evidence": evidence or ["no trend evidence yet — available as a manual template"],
                    "suggested_clip_ids": [str(c.id) for c in fit[: t.max_clips]],
                    "ready": len(fit) >= t.min_clips, "text_hint": t.text_hint})
    return sorted(out, key=lambda x: x["score"], reverse=True)


def _usable_clips(db: Session) -> list[Clip]:
    return list(db.scalars(select(Clip).where(
        Clip.origin == ClipOrigin.stream_clip, Clip.edit_status == EditStatus.rendered,
        Clip.verification == Verification.verified, Clip.media_source.in_(AUTHORIZED_MEDIA))
        .order_by(Clip.quality_score.desc().nullslast()).limit(200)).all())


def _fitting_clips(t: Template, clips: list[Clip], by_cat: Counter[str]) -> list[Clip]:
    if t.key == "compilation":
        cat = by_cat.most_common(1)[0][0] if by_cat else None
        return [c for c in clips if c.category == cat]
    if t.key == "storytime":
        return [c for c in clips if c.category == "storytelling"]
    if t.key in ("how_me_and_bro_look", "reaction"):
        return [c for c in clips if c.category in ("funny", "reaction", "unexpected", "hype_gameplay")]
    return clips


def _template_performance(db: Session) -> dict[str, dict[str, Any]]:
    edits = db.scalars(select(ViralEdit).where(ViralEdit.output_clip_id.isnot(None))).all()
    out: dict[str, dict[str, Any]] = {}
    for e in edits:
        snaps = db.scalars(select(AnalyticsSnapshot).where(AnalyticsSnapshot.clip_id == e.output_clip_id)
                           .order_by(AnalyticsSnapshot.captured_at.desc()).limit(1)).all()
        if snaps and snaps[0].views is not None:
            d = out.setdefault(e.template_key, {"posts": 0, "views": 0})
            d["posts"] += 1
            d["views"] += snaps[0].views
    return {k: {"posts": v["posts"], "avg_views": v["views"] // v["posts"]} for k, v in out.items()}


# ------------------------------------------------------------------------------------ create/render
def create_edit(db: Session, template_key: str, clip_ids: list[uuid.UUID], text: str, *, actor: str,
                viral_source_id: uuid.UUID | None = None) -> ViralEdit:
    t = TEMPLATES.get(template_key)
    if t is None:
        raise ValueError("unknown template")
    if not t.min_clips <= len(clip_ids) <= t.max_clips:
        raise ValueError(f"{t.name} needs {t.min_clips}-{t.max_clips} clips")
    clips = [db.get(Clip, cid) for cid in clip_ids]
    for c in clips:
        if c is None:
            raise ValueError("clip not found")
        if c.media_source not in AUTHORIZED_MEDIA or c.verification != Verification.verified:
            raise PermissionError(f"clip '{c.title}' has no authorized, verified media and cannot be reused")
        if c.edit_status != EditStatus.rendered or not c.rendered_key:
            raise ValueError(f"clip '{c.title}' is not rendered yet")
    edit = ViralEdit(template_key=template_key, params={"text": text[:120]}, input_clip_ids=[str(c) for c in clip_ids],
                     viral_source_id=viral_source_id, status="queued", suggested_by=actor)
    db.add(edit)
    db.flush()
    jobs.enqueue(db, "render_viral_edit", f"viral:{edit.id}", ref_id=str(edit.id), payload={"edit_id": str(edit.id)})
    return edit


def _overlay_ass(lines: list[tuple[str, int, int, str, str, float, float | None]]) -> str:
    """lines: (text, y, size, fg_hex, box_hex|'' , start, end|None)."""
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, \
Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, \
MarginV, Encoding
Style: Plain,Inter ExtraBold,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,6,2,5,60,60,0,1
Style: Boxed,Inter ExtraBold,60,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,18,0,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    for text, y, size, fg, box, start, end in lines:
        style = "Boxed" if box else "Plain"
        colors = f"\\c{_ass_color(fg)}" + (f"\\3c{_ass_color(box)}" if box else "")
        e = "9:59:59.00" if end is None else _t(end)
        ev.append(f"Dialogue: 1,{_t(start)},{e},{style},,0,0,0,,{{\\an5\\pos({W // 2},{y})\\fs{size}{colors}"
                  f"\\fad(120,0)}}{_escape(text)}")
    return head + "\n".join(ev) + "\n"


def _t(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"0:{int(m):02d}:{s:05.2f}"


def _wrap(text: str, width: int = 26) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\\N".join(lines[:3])


def _duration(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True, check=True, timeout=60).stdout
    return float(out.strip() or 0)


def build_filter(template_key: str, inputs: list[Path], text: str) -> tuple[str, list[tuple], float]:
    """Return (filter_complex, overlay lines, duration). Inputs are 1080x1920 renders."""
    durs = [_duration(p) for p in inputs]
    overlays: list[tuple] = []
    if template_key in ("pov", "storytime"):
        label = f"POV: {text}" if template_key == "pov" else f"STORYTIME: {text}" if text else "STORYTIME"
        overlays.append((_wrap(label), 230, 62, "#000000", "#FFFFFF", 0.0, None))
        return "[0:v]null[v];[0:a]anull[a]", overlays, durs[0]
    if template_key in ("how_me_and_bro_look", "before_after"):
        d = min(durs[0], durs[1], 30.0)
        f = (f"[0:v]trim=0:{d},setpts=PTS-STARTPTS,crop=1080:960:0:520[t];"
             f"[1:v]trim=0:{d},setpts=PTS-STARTPTS,crop=1080:960:0:520[b];[t][b]vstack[v];"
             f"[0:a]atrim=0:{d},asetpts=PTS-STARTPTS[a0];[1:a]atrim=0:{d},asetpts=PTS-STARTPTS,volume=0.35[a1];"
             f"[a0][a1]amix=inputs=2:normalize=0[a]")
        if template_key == "how_me_and_bro_look":
            overlays.append((_wrap(f"How me and bro look {text}".strip()), 120, 56, "#000000", "#FFFFFF", 0.0, None))
        else:
            overlays.append(("BEFORE", 900, 64, "#FFFFFF", "", 0.0, None))
            overlays.append(("AFTER", 1020, 64, "#FFE11A", "", 0.0, None))
        return f, overlays, d
    if template_key == "reaction":
        d = min(durs[0], 45.0)
        f = (f"[0:v]trim=0:{d},setpts=PTS-STARTPTS[m];[1:v]trim=0:{d},setpts=PTS-STARTPTS,"
             f"crop=1080:1080:0:300,scale=420:420[p];[m][p]overlay=W-w-40:220:shortest=1[v];"
             f"[0:a]atrim=0:{d},asetpts=PTS-STARTPTS[a]")
        if text:
            overlays.append((_wrap(text), 120, 52, "#000000", "#FFFFFF", 0.0, None))
        return f, overlays, d
    # compilation: first ~9 s of each clip, numbered
    seg = [min(9.0, x) for x in durs]
    parts, t = [], 0.0
    for i, d in enumerate(seg):
        parts.append(f"[{i}:v]trim=0:{d},setpts=PTS-STARTPTS[v{i}];[{i}:a]atrim=0:{d},asetpts=PTS-STARTPTS[a{i}]")
        overlays.append((f"#{len(seg) - i}", 360, 120, "#FFE11A", "", t, t + min(1.6, d)))
        t += d
    cat = "".join(f"[v{i}][a{i}]" for i in range(len(seg)))
    f = ";".join(parts) + f";{cat}concat=n={len(seg)}:v=1:a=1[v][a]"
    if text:
        overlays.append((_wrap(text), 160, 56, "#000000", "#FFFFFF", 0.0, None))
    return f, overlays, t


def render_edit(db: Session, edit_id: uuid.UUID) -> dict[str, Any]:
    edit = db.get(ViralEdit, edit_id)
    if edit is None:
        raise jobs.PermanentFailure("edit not found")
    if edit.output_clip_id:
        return {"skipped": True}
    clips = [c for c in (db.get(Clip, uuid.UUID(x)) for x in edit.input_clip_ids) if c is not None]
    if len(clips) != len(edit.input_clip_ids):
        raise jobs.PermanentFailure("an input clip was deleted")
    storage = get_storage()
    edit.status = "rendering"
    db.commit()
    with tempfile.TemporaryDirectory(prefix="clipx-viral-") as tmpd:
        tmp = Path(tmpd)
        inputs = [storage.get_file(c.rendered_key or "", tmp / f"in{i}.mp4") for i, c in enumerate(clips) if c]
        fc, overlays, duration = build_filter(edit.template_key, inputs, edit.params.get("text", ""))
        (tmp / "ov.ass").write_text(_overlay_ass(overlays), encoding="utf-8")
        fc = fc.replace("[v]", "[vb]", 1) if "[v]" in fc else fc
        fc += ";[vb]ass=ov.ass,format=yuv420p[v];[a]loudnorm=I=-14:TP=-1.5:LRA=11[ao]"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        for p in inputs:
            cmd += ["-i", str(p)]
        cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[ao]", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "19", "-r", "30", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
                "-t", f"{duration:.3f}", "out.mp4"]
        r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            edit.status, edit.error = "failed", r.stderr[-1500:]
            raise RuntimeError(f"viral render failed: {r.stderr[-500:]}")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "1.0", "-i", "out.mp4",
                        "-frames:v", "1", "-q:v", "3", "thumb.jpg"], cwd=tmp, check=True, timeout=60)
        t = TEMPLATES[edit.template_key]
        first = clips[0]
        out = Clip(origin=ClipOrigin.viral_edit, title=f"{t.name}: {edit.params.get('text') or first.title}"[:200],
                   category=first.category if first else "funny",
                   quality_score=max((c.quality_score or 0) for c in clips if c),
                   verification=Verification.verified, duration_s=round(duration, 2), media_source=MediaSource.composed,
                   rights_basis="Composed from CLIPX clips with authorized media: " + ", ".join(edit.input_clip_ids),
                   edit_status=EditStatus.rendered,
                   transcript_text=" ".join(c.transcript_text or "" for c in clips)[:4000],
                   render_meta={"template": edit.template_key, "inputs": edit.input_clip_ids})
        db.add(out)
        db.flush()
        key, thumb = f"clips/{out.id}/render_v1.mp4", f"clips/{out.id}/thumb_v1.jpg"
        storage.put_file(key, tmp / "out.mp4", "video/mp4")
        storage.put_file(thumb, tmp / "thumb.jpg", "image/jpeg")
    out.rendered_key, out.thumbnail_key = key, thumb
    edit.output_clip_id, edit.status, edit.error = out.id, "rendered", None
    jobs.enqueue(db, "caption_clip", f"caption_clip:{out.id}:v1", ref_id=str(out.id), payload={"clip_id": str(out.id)})
    return {"clip_id": str(out.id), "duration": duration}
