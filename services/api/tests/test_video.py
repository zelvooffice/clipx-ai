"""Rendering + subtitle engine (real FFmpeg/OpenCV)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from app.detection.signals import FaceBox
from app.integrations.tiktok.client import MB, chunk_plan, chunk_ranges
from app.video import edit_plan, reframe, subtitles
from app.video.render import RenderOptions, render
from tests.conftest import make_video


def _words(text: str, start: float = 0.5, step: float = 0.4) -> list[dict]:
    out, t = [], start
    for w in text.split():
        out.append({"w": w, "start": round(t, 3), "end": round(t + step * 0.8, 3), "p": 0.9})
        t += step
    return out


# ------------------------------------------------------------------------------ subtitles
def test_cues_follow_word_timestamps_exactly() -> None:
    words = _words("No way bro, did you SEE that? That was actually insane! I can't believe it worked.")
    cues = subtitles.build_cues(words, keywords=["insane"], clip_end=12)
    flat = [w for c in cues for w in c.words]
    assert [w["w"] for w in flat] == [w["w"] for w in words]  # every word, in order, once
    for c in cues:
        assert c.start == pytest.approx(c.words[0]["start"])  # appears exactly when spoken
        assert c.end >= c.words[-1]["end"] - 1e-6
        assert len(c.text) <= 20 or len(c.words) == 1
    for a, b in zip(cues, cues[1:], strict=False):
        assert a.end <= b.start + 1e-6  # never overlapping


def test_emphasis_is_selective() -> None:
    words = _words(" ".join(["normal"] * 40) + " INSANE! amazing incredible", step=0.3)
    cues = subtitles.build_cues(words, keywords=["incredible"])
    emph = [w["w"] for c in cues for w in c.words if w["emph"]]
    assert 1 <= len(emph) <= max(1, int(len(words) * 0.12))
    assert "incredible" in emph


def test_cues_avoid_faces() -> None:
    cues = subtitles.build_cues(_words("hello there my friend."), clip_end=5)
    face_lower = [(t / 5, 300.0, 1250.0, 400.0, 300.0) for t in range(25)]  # face in the lower zone throughout
    subtitles.place_cues(cues, face_lower)
    assert all(c.zone != "lower" for c in cues)


def test_ass_and_srt_output() -> None:
    cues = subtitles.build_cues(_words("Hello {there} chat!"), keywords=["chat"])
    ass = subtitles.to_ass(cues)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "&H001AE1FF" in ass  # bright yellow #FFE11A in ASS BGR order
    assert "{there}" not in ass  # override tags in user text are neutralised
    assert "00:00:00,500 -->" in subtitles.to_srt(cues)


# ------------------------------------------------------------------------------ layout + plan
def _analysis(faces: list[FaceBox], duration: float = 10) -> reframe.RenderAnalysis:
    return reframe.RenderAnalysis(1920, 1080, 30, duration, faces, np.zeros(int(duration)))


def test_layout_decisions() -> None:
    big = [FaceBox(t / 2.5, 0.4, 0.2, 0.18, 0.32) for t in range(25)]
    assert reframe.decide_layout(_analysis(big)).kind == "track"
    cam = [FaceBox(t / 2.5, 0.82, 0.72, 0.06, 0.1) for t in range(25)]
    lay = reframe.decide_layout(_analysis(cam))
    assert lay.kind == "stacked" and lay.facecam_box is not None
    assert reframe.decide_layout(_analysis([])).kind == "fit"
    assert reframe.decide_layout(_analysis(cam), "fit").kind == "fit"  # editor override


def test_crop_path_is_smooth_and_bounded() -> None:
    faces = [FaceBox(t / 5, 0.2 + 0.4 * (t > 25), 0.3, 0.2, 0.3) for t in range(50)]  # subject jumps sideways
    lay = reframe.decide_layout(_analysis(faces))
    path = reframe.crop_path(_analysis(faces), lay, 300, 30)
    assert np.max(np.abs(np.diff(path))) < 0.03  # no jumps between frames
    assert abs(path[-1] - 0.7) < 0.05  # eventually follows the subject
    assert path.max() <= 0.7 + 1e-3  # no overshoot


def test_edit_plans_vary_by_clip_and_category_but_are_deterministic() -> None:
    cues = [subtitles.Cue(t, t + 1, [{"w": "x", "start": t, "end": t + 0.5, "emph": True}]).as_dict()
            for t in range(2, 30, 3)]
    a = edit_plan.build_plan(clip_id="a", category="funny", duration=32, cues=cues, audio_peaks=[5, 12])
    a2 = edit_plan.build_plan(clip_id="a", category="funny", duration=32, cues=cues, audio_peaks=[5, 12])
    b = edit_plan.build_plan(clip_id="b", category="funny", duration=32, cues=cues, audio_peaks=[5, 12])
    story = edit_plan.build_plan(clip_id="a", category="storytelling", duration=32, cues=cues, audio_peaks=[5])
    assert a.as_dict() == a2.as_dict()
    assert a.as_dict() != b.as_dict()
    assert len(story.zooms) < len(a.zooms) and story.push_in > 1.0 and not story.sfx
    off = edit_plan.build_plan(clip_id="a", category="funny", duration=32, cues=cues, audio_peaks=[], zoom_intensity=0)
    assert off.zooms == [] and off.sfx == []


def test_tiktok_chunk_plan_rules() -> None:
    assert chunk_plan(3 * MB) == (3 * MB, 1)  # < 5 MB uploaded whole
    assert chunk_plan(40 * MB) == (40 * MB, 1)
    size = 150 * MB + 123
    chunk, count = chunk_plan(size)
    ranges = chunk_ranges(size, chunk, count)
    assert count == size // chunk and 5 * MB <= chunk <= 64 * MB
    assert ranges[0][0] == 0 and ranges[-1][1] == size - 1
    assert ranges[-1][1] - ranges[-1][0] + 1 <= 128 * MB
    assert all(b[0] == a[1] + 1 for a, b in zip(ranges, ranges[1:], strict=False))


# ------------------------------------------------------------------------------ full render (real ffmpeg)
def _probe(p: Path) -> dict:
    return json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
                                      str(p)], capture_output=True, text=True, check=True).stdout)


def _lufs(p: Path) -> float:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(p), "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    line = [x for x in err.splitlines() if x.strip().startswith("I:")][-1]
    return float(line.split()[1])


@pytest.mark.media
def test_render_produces_tiktok_ready_mp4(tmp_path: Path) -> None:
    src = make_video(tmp_path / "src.mp4", seconds=10)
    words = _words("Bro what was that? That was insane! No way it actually worked.")
    res = render(src, tmp_path / "out.mp4", words,
                 RenderOptions(clip_id="c1", category="funny", keywords=["insane"], trim_start=1.0, trim_end=9.0))
    info = _probe(res.output)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (v["codec_name"], v["width"], v["height"], v["pix_fmt"]) == ("h264", 1080, 1920, "yuv420p")
    assert v["r_frame_rate"] == "30/1"
    assert a["codec_name"] == "aac" and a["sample_rate"] == "48000"
    assert abs(float(info["format"]["duration"]) - 8.0) < 0.15
    assert abs(_lufs(res.output) - (-14.0)) < 1.5
    assert res.thumbnail.exists() and res.cues and res.cues[0]["start"] == pytest.approx(0.5 - 1.0 + 0.4, abs=0.5)


@pytest.mark.media
def test_render_without_audio_and_edited_cues(tmp_path: Path) -> None:
    src = make_video(tmp_path / "silent.mp4", seconds=5, tone=False)
    cues = [subtitles.Cue(0.5, 2.0, [{"w": "Edited", "start": 0.5, "end": 1.0, "emph": False},
                                     {"w": "caption", "start": 1.0, "end": 2.0, "emph": True}], "upper").as_dict()]
    res = render(src, tmp_path / "o.mp4", [], RenderOptions(clip_id="c2", cues_override=cues, layout="fit"))
    assert any(s["codec_type"] == "audio" for s in _probe(res.output)["streams"])
    assert res.cues[0]["zone"] == "upper"
