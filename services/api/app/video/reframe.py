"""Intelligent 9:16 reframing: layout decision, face/subject tracking crop path, frame composition."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from app.detection.signals import FaceBox, visual_features

OUT_W, OUT_H = 1080, 1920
TOP_H = 700  # stacked layout: facecam band height
BOTTOM_H = OUT_H - TOP_H


@dataclass
class RenderAnalysis:
    width: int
    height: int
    fps: float
    duration: float
    faces: list[FaceBox]
    motion: np.ndarray
    sample_fps: float = 5.0


@dataclass
class FaceCluster:
    cx: float
    cy: float
    w: float
    h: float
    persistence: float  # fraction of sampled frames containing this face
    track: list[tuple[float, float, float]] = field(default_factory=list)  # (t, cx, cy)


@dataclass
class Layout:
    kind: str  # track | stacked | fit
    reason: str
    face: FaceCluster | None = None
    facecam_box: tuple[float, float, float, float] | None = None  # normalised x, y, w, h (stacked)


def analyze(path: Path, start: float | None = None, duration: float | None = None) -> RenderAnalysis:
    vf = visual_features(path, sample_fps=5.0, start=start, duration=duration, detect_faces=True)
    dur = duration if duration is not None else len(vf.motion)
    return RenderAnalysis(vf.width, vf.height, vf.fps, float(dur), vf.faces, vf.motion)


def cluster_faces(faces: list[FaceBox], duration: float, sample_fps: float = 2.5) -> list[FaceCluster]:
    """Group detections by position (face cams sit still; just-chatting faces move a little)."""
    clusters: list[dict] = []
    for f in faces:
        cx, cy = f.x + f.w / 2, f.y + f.h / 2
        for c in clusters:
            if abs(c["cx"] - cx) < max(0.12, c["w"]) and abs(c["cy"] - cy) < max(0.12, c["h"]):
                n = c["n"]
                c["cx"] = (c["cx"] * n + cx) / (n + 1)
                c["cy"] = (c["cy"] * n + cy) / (n + 1)
                c["w"] = (c["w"] * n + f.w) / (n + 1)
                c["h"] = (c["h"] * n + f.h) / (n + 1)
                c["n"] += 1
                c["times"].add(round(f.t, 1))
                c["track"].append((f.t, cx, cy))
                break
        else:
            clusters.append({"cx": cx, "cy": cy, "w": f.w, "h": f.h, "n": 1, "times": {round(f.t, 1)},
                             "track": [(f.t, cx, cy)]})
    expected = max(1.0, duration * sample_fps)
    out = [FaceCluster(c["cx"], c["cy"], c["w"], c["h"], min(1.0, len(c["times"]) / expected), c["track"])
           for c in clusters]
    return sorted(out, key=lambda c: c.persistence * c.w * c.h, reverse=True)


def decide_layout(a: RenderAnalysis, override: str = "auto") -> Layout:
    clusters = cluster_faces(a.faces, a.duration)
    main = clusters[0] if clusters and clusters[0].persistence >= 0.35 else None
    src_aspect = a.width / max(1, a.height)
    if override in ("track", "stacked", "fit"):
        return _forced(override, main)
    if src_aspect < 0.8:
        return Layout("fit", "source is already vertical", main)
    if main is None:
        return Layout("fit", "no persistent face found: full frame on blurred background", None)
    if main.h >= 0.2:
        return Layout("track", f"large face (h={main.h:.2f}) present {main.persistence:.0%} of the time", main)
    near_edge = main.cx < 0.33 or main.cx > 0.67 or main.cy < 0.33 or main.cy > 0.67
    if near_edge:
        bw, bh = min(0.5, main.w * 3.2), min(0.6, main.h * 3.2)
        bx = float(np.clip(main.cx - bw / 2, 0, 1 - bw))
        by = float(np.clip(main.cy - bh / 2, 0, 1 - bh))
        return Layout("stacked", "small face in a corner (facecam over gameplay)", main, (bx, by, bw, bh))
    return Layout("track", "medium face near centre", main)


def _forced(kind: str, main: FaceCluster | None) -> Layout:
    if kind == "stacked" and main is not None:
        bw, bh = min(0.5, main.w * 3.2), min(0.6, main.h * 3.2)
        return Layout("stacked", "forced by editor", main,
                      (float(np.clip(main.cx - bw / 2, 0, 1 - bw)), float(np.clip(main.cy - bh / 2, 0, 1 - bh)),
                       bw, bh))
    if kind == "stacked":
        return Layout("fit", "stacked requested but no facecam found", None)
    return Layout(kind, "forced by editor", main)


def crop_path(a: RenderAnalysis, layout: Layout, n_frames: int, fps: float, *, override_x: float | None = None
              ) -> np.ndarray:
    """Per-output-frame horizontal crop centre (normalised 0..1), smoothed with a dead zone."""
    if override_x is not None:
        return np.full(n_frames, float(override_x), dtype=np.float32)
    if layout.kind == "track" and a.faces:
        ts_list, xs_list = [], []
        prev = layout.face.cx if layout.face else 0.5
        by_t: dict[float, list[FaceBox]] = {}
        for f in a.faces:
            by_t.setdefault(round(f.t, 2), []).append(f)
        for t in sorted(by_t):
            # follow the biggest face, preferring the one closest to where we already are (no flip-flopping)
            best = max(by_t[t], key=lambda f: f.w * f.h * (1.5 if abs(f.x + f.w / 2 - prev) < 0.15 else 1.0))
            prev = best.x + best.w / 2
            ts_list.append(t)
            xs_list.append(prev)
        ts, xs = np.array(ts_list), np.array(xs_list)
    else:
        # follow where the action is (horizontal motion centroid is approximated by frame centre)
        return np.full(n_frames, 0.5, dtype=np.float32)
    t_out = np.arange(n_frames) / fps
    target = np.interp(t_out, ts, xs)
    out = np.empty(n_frames, dtype=np.float32)
    cur = float(target[0])
    vel = 0.0
    dead, gain, max_step = 0.04, 0.08, 0.018  # dead zone, response, max pan speed (frame widths/frame)
    for i, tgt in enumerate(target):
        err = float(tgt) - cur
        # proportional step towards the target (cannot overshoot), speed-capped, with eased acceleration
        desired = 0.0 if abs(err) <= dead else float(np.clip(gain * err, -max_step, max_step))
        if desired and abs(desired) > abs(err):
            desired = err
        vel = 0.8 * vel + 0.2 * desired
        if (err > 0 and vel > err) or (err < 0 and vel < err):
            vel = err
        cur += vel
        out[i] = cur
    return out


@dataclass
class Composer:
    a: RenderAnalysis
    layout: Layout

    def __post_init__(self) -> None:
        self.sw, self.sh = self.a.width, self.a.height

    def compose(self, frame: np.ndarray, cx: float, zoom: float) -> np.ndarray:
        k = self.layout.kind
        if k == "track":
            return self._track(frame, cx, zoom)
        if k == "stacked":
            return self._stacked(frame, zoom)
        return self._fit(frame, zoom)

    def _crop(self, frame: np.ndarray, cx: float, cy: float, aspect: float, zoom: float,
              region: tuple[int, int, int, int] | None = None) -> np.ndarray:
        x0, y0, rw, rh = region or (0, 0, frame.shape[1], frame.shape[0])
        if rw / rh > aspect:
            ch = rh / zoom
            cw = ch * aspect
        else:
            cw = rw / zoom
            ch = cw / aspect
        px = x0 + cx * rw - cw / 2
        py = y0 + cy * rh - ch / 2
        px = float(np.clip(px, x0, x0 + rw - cw))
        py = float(np.clip(py, y0, y0 + rh - ch))
        return frame[int(py): int(py + ch), int(px): int(px + cw)]

    def _track(self, frame: np.ndarray, cx: float, zoom: float) -> np.ndarray:
        cy = self.layout.face.cy if self.layout.face else 0.5
        crop = self._crop(frame, cx, min(0.6, max(0.4, cy + 0.08)), OUT_W / OUT_H, zoom)
        return cv2.resize(crop, (OUT_W, OUT_H), interpolation=cv2.INTER_LINEAR)

    def _stacked(self, frame: np.ndarray, zoom: float) -> np.ndarray:
        assert self.layout.facecam_box is not None
        bx, by, bw, bh = self.layout.facecam_box
        region = (int(bx * self.sw), int(by * self.sh), int(bw * self.sw), int(bh * self.sh))
        top = self._crop(frame, 0.5, 0.5, OUT_W / TOP_H, 1.0, region)
        bottom = self._crop(frame, 0.5, 0.5, OUT_W / BOTTOM_H, zoom)
        out = np.empty((OUT_H, OUT_W, 3), dtype=np.uint8)
        out[:TOP_H] = cv2.resize(top, (OUT_W, TOP_H), interpolation=cv2.INTER_LINEAR)
        out[TOP_H:] = cv2.resize(bottom, (OUT_W, BOTTOM_H), interpolation=cv2.INTER_LINEAR)
        out[TOP_H - 3: TOP_H + 3] = (18, 18, 18)  # subtle divider
        return out

    def _fit(self, frame: np.ndarray, zoom: float) -> np.ndarray:
        # blur at 1/8 resolution then upscale: visually identical, ~50x cheaper than a full-res blur
        small = cv2.resize(frame, (OUT_W // 8, OUT_H // 8), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), 2.5)
        bg = cv2.resize(cv2.convertScaleAbs(small, alpha=0.45), (OUT_W, OUT_H), interpolation=cv2.INTER_LINEAR)
        fg_w = int(OUT_W * zoom)
        fg_h = int(fg_w * self.sh / self.sw)
        fg = cv2.resize(frame, (fg_w, fg_h), interpolation=cv2.INTER_LINEAR)
        x0 = (fg_w - OUT_W) // 2
        fg = fg[:, x0: x0 + OUT_W]
        if fg.shape[0] > OUT_H:  # very tall source: centre-crop vertically
            y0 = (fg.shape[0] - OUT_H) // 2
            fg = fg[y0: y0 + OUT_H]
        y = (OUT_H - fg.shape[0]) // 2
        bg[y: y + fg.shape[0], :] = fg
        return bg

    def face_box_out(self, t: float, cx: float) -> tuple[float, float, float, float] | None:
        """Approximate face position in output pixels at time t (for subtitle placement)."""
        f = self.layout.face
        if f is None:
            return None
        if self.layout.kind == "stacked":
            return (0.0, 60.0, float(OUT_W), float(TOP_H - 120))
        if self.layout.kind == "track":
            crop_w = self.sh * OUT_W / OUT_H
            scale = OUT_W / crop_w
            left = cx * self.sw - crop_w / 2
            x = (f.cx * self.sw - f.w * self.sw / 2 - left) * scale
            y = (f.cy - f.h / 2) * self.sh * (OUT_H / self.sh)
            return (x, y, f.w * self.sw * scale, f.h * self.sh * (OUT_H / self.sh))
        fg_h = OUT_W * self.sh / self.sw
        y0 = (OUT_H - fg_h) / 2
        return (f.cx * OUT_W - f.w * OUT_W / 2, y0 + (f.cy - f.h / 2) * fg_h, f.w * OUT_W, f.h * fg_h)
