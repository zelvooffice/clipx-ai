"""Render a clip to 1080x1920 H.264/AAC MP4 with reframing, punch-ins, sound design and subtitles."""

from __future__ import annotations

import logging
import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.detection.signals import audio_features, load_audio, probe_video, robust_z
from app.video import edit_plan, reframe, subtitles

log = logging.getLogger("clipx.render")
FPS = 30


@dataclass
class RenderOptions:
    clip_id: str
    category: str = "reaction"
    keywords: list[str] | None = None
    layout: str = "auto"
    zoom_intensity: float = 1.0
    sfx_enabled: bool = True
    subtitle_style: dict[str, Any] | None = None
    crop_x: float | None = None
    trim_start: float = 0.0
    trim_end: float | None = None
    version: int = 1
    preset: str = "veryfast"
    crf: int = 19
    cues_override: list[dict[str, Any]] | None = None  # edited subtitle track


@dataclass
class RenderResult:
    output: Path
    thumbnail: Path
    duration: float
    layout: dict[str, Any]
    plan: dict[str, Any]
    cues: list[dict[str, Any]]


def has_audio(path: Path) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60).stdout
    return bool(out.strip())


def audio_peaks(path: Path, start: float, duration: float, top: int = 6) -> list[float]:
    if not has_audio(path):
        return []
    af = audio_features(load_audio(path, start=start, duration=duration))
    if af.seconds == 0:
        return []
    score = robust_z(af.rms_db) + 3 * af.burst
    idx = [int(i) for i in np.argsort(score)[::-1][:top] if score[int(i)] > 1.0]
    return sorted(float(i) + 0.4 for i in idx)


def render(src: Path, out: Path, words: list[dict[str, Any]], opts: RenderOptions) -> RenderResult:
    sw, sh, _, src_dur = probe_video(src)
    start = max(0.0, opts.trim_start)
    end = min(src_dur, opts.trim_end) if opts.trim_end else src_dur
    duration = round(end - start, 3)
    if duration < 1.0:
        raise ValueError(f"clip too short to render ({duration:.2f}s)")

    analysis = reframe.analyze(src, start=start, duration=duration)
    layout = reframe.decide_layout(analysis, opts.layout)
    composer = reframe.Composer(analysis, layout)
    n_frames = int(math.ceil(duration * FPS))
    cx_path = reframe.crop_path(analysis, layout, n_frames + FPS, FPS, override_x=opts.crop_x)

    rel_words = [{**w, "start": w["start"] - start, "end": w["end"] - start} for w in words
                 if w["end"] > start and w["start"] < end]
    if opts.cues_override is not None:
        cues = [subtitles.Cue.from_dict(c) for c in opts.cues_override]
    else:
        cues = subtitles.build_cues(rel_words, style=opts.subtitle_style, keywords=opts.keywords,
                                    clip_end=duration)
    plan = edit_plan.build_plan(clip_id=opts.clip_id, category=opts.category, duration=duration,
                                cues=[c.as_dict() for c in cues],
                                audio_peaks=audio_peaks(src, start, duration),
                                zoom_intensity=opts.zoom_intensity, sfx_enabled=opts.sfx_enabled,
                                version=opts.version)
    if opts.cues_override is None:
        faces_out = []
        for f in analysis.faces:
            i = min(len(cx_path) - 1, int(f.t * FPS))
            box = composer.face_box_out(f.t, float(cx_path[i]))
            if box:
                faces_out.append((f.t, *box))
        subtitles.place_cues(cues, faces_out)

    with tempfile.TemporaryDirectory(prefix="clipx-render-") as tmpd:
        tmp = Path(tmpd)
        (tmp / "subs.ass").write_text(subtitles.to_ass(cues, opts.subtitle_style), encoding="utf-8")
        sfx_inputs: list[tuple[Path, edit_plan.Sfx]] = []
        for i, s in enumerate(plan.sfx):
            p = edit_plan.write_sfx(s.kind, tmp / f"sfx{i}.wav")
            sfx_inputs.append((p, s))
        _encode(src, out, tmp, start, duration, n_frames, composer, cx_path, plan, sfx_inputs, opts)

    thumb = out.with_suffix(".jpg")
    t_thumb = next((w["start"] for c in cues for w in c.words if w.get("emph")), duration * 0.35)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{min(t_thumb, duration - 0.1):.2f}",
                    "-i", str(out), "-frames:v", "1", "-q:v", "3", str(thumb)], check=True, timeout=60)
    return RenderResult(out, thumb, duration,
                        {"kind": layout.kind, "reason": layout.reason, "source": [sw, sh]},
                        plan.as_dict(), [c.as_dict() for c in cues])


def _encode(src: Path, out: Path, tmp: Path, start: float, duration: float, n_frames: int,
            composer: reframe.Composer, cx_path: np.ndarray, plan: edit_plan.EditPlan,
            sfx_inputs: list[tuple[Path, edit_plan.Sfx]], opts: RenderOptions) -> None:
    sw, sh = composer.sw, composer.sh
    dec_cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{start:.3f}", "-i", str(src),
               "-t", f"{duration:.3f}", "-vf", f"fps={FPS}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    audio_in = (["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(src)] if has_audio(src)
                else ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"])
    enc_cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
               "-s", f"{reframe.OUT_W}x{reframe.OUT_H}", "-r", str(FPS), "-i", "pipe:0", *audio_in]
    for p, _ in sfx_inputs:
        enc_cmd += ["-i", str(p)]
    fo = max(0.0, duration - plan.fade_out)
    vf = (f"[0:v]ass=subs.ass,fade=t=in:st=0:d={plan.fade_in},fade=t=out:st={fo:.3f}:d={plan.fade_out},"
          "format=yuv420p[v]")
    af = ["[1:a]aresample=48000,aformat=channel_layouts=stereo[a0]"]
    mix = ["[a0]"]
    for i, (_, s) in enumerate(sfx_inputs):
        ms = int(s.t * 1000)
        af.append(f"[{i + 2}:a]aformat=channel_layouts=stereo,adelay={ms}|{ms},volume={s.gain_db}dB[s{i}]")
        mix.append(f"[s{i}]")
    am = f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first[am]" if len(mix) > 1 else "[a0]anull[am]"
    af.append(am)
    af.append(f"[am]loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=in:d={plan.fade_in},"
              f"afade=t=out:st={fo:.3f}:d={plan.fade_out}[a]")
    enc_cmd += ["-filter_complex", ";".join([vf, *af]), "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", opts.preset, "-crf", str(opts.crf), "-profile:v", "high",
                "-level", "4.1", "-r", str(FPS), "-g", str(FPS * 2), "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-t", f"{duration:.3f}", "-movflags", "+faststart", str(out.resolve())]

    dec = subprocess.Popen(dec_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, cwd=tmp)
    assert dec.stdout is not None and enc.stdin is not None
    frame_bytes = sw * sh * 3
    i = 0
    last = None
    try:
        while i < n_frames:
            buf = dec.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(sh, sw, 3)
            t = i / FPS
            last = composer.compose(frame, float(cx_path[min(i, len(cx_path) - 1)]), plan.zoom_at(t, duration))
            enc.stdin.write(np.ascontiguousarray(last).tobytes())
            i += 1
        while last is not None and i < n_frames:  # pad a few frames if the decoder ended early
            enc.stdin.write(np.ascontiguousarray(last).tobytes())
            i += 1
    except BrokenPipeError:
        pass
    finally:
        enc.stdin.close()
        dec.stdout.close()
        dec.wait(timeout=60)
        rc = enc.wait(timeout=900)
    if rc != 0 or i == 0:
        err = enc.stderr.read().decode(errors="replace")[-2000:] if enc.stderr else ""
        raise RuntimeError(f"encoder failed (rc={rc}, frames={i}): {err}")
