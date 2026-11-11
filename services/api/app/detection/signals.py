"""Low-level signal extraction: audio, visual and chat features on a per-second timeline.

Each extractor returns a per-second series (index = second since window start) plus a summary.
Scores are robust z-scores against a baseline, so "loud" is always relative to the stream's own normal
level — and loudness is just one input among several (see scoring.py).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

SR = 16000


# ------------------------------------------------------------------------------------------- audio
def load_audio(path: Path, *, start: float | None = None, duration: float | None = None) -> np.ndarray:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True, timeout=600).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


@dataclass
class AudioFeatures:
    rms_db: np.ndarray  # per second
    flux: np.ndarray  # spectral flux per second (onsets / sudden changes)
    burst: np.ndarray  # fraction of 50 ms frames well above the local median (laughter/shouting texture)
    voiced: np.ndarray  # rough voice-activity ratio per second

    @property
    def seconds(self) -> int:
        return len(self.rms_db)


def audio_features(samples: np.ndarray) -> AudioFeatures:
    n_sec = int(len(samples) // SR)
    if n_sec == 0:
        z = np.zeros(0, dtype=np.float32)
        return AudioFeatures(z, z, z, z)
    hop = SR // 20  # 50 ms frames
    frames = samples[: n_sec * SR].reshape(-1, hop)
    frame_rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-10)
    frame_db = 20 * np.log10(frame_rms + 1e-9)
    per_sec_db = frame_db.reshape(n_sec, 20)
    rms_db = per_sec_db.max(axis=1)

    # spectral flux on 50 ms frames
    win = np.hanning(hop)
    spec = np.abs(np.fft.rfft(frames * win, axis=1))
    diff = np.maximum(0.0, np.diff(spec, axis=0, prepend=spec[:1]))
    flux_frames = diff.sum(axis=1) / (spec.sum(axis=1) + 1e-6)
    flux = flux_frames.reshape(n_sec, 20).mean(axis=1)

    med = float(np.median(frame_db))
    burst = (per_sec_db > med + 12).mean(axis=1)

    # crude voice activity: energy above floor and zero-crossing rate in a speech-like band
    zcr = (np.abs(np.diff(np.sign(frames), axis=1)) > 0).mean(axis=1)
    floor = np.percentile(frame_db, 20)
    voiced_frames = (frame_db > floor + 6) & (zcr > 0.02) & (zcr < 0.35)
    voiced = voiced_frames.reshape(n_sec, 20).mean(axis=1)
    return AudioFeatures(rms_db.astype(np.float32), flux.astype(np.float32), burst.astype(np.float32),
                         voiced.astype(np.float32))


# ------------------------------------------------------------------------------------------- video
@lru_cache
def _face_cascade() -> cv2.CascadeClassifier:
    cascades = Path(cv2.data.haarcascades)  # type: ignore[attr-defined]
    return cv2.CascadeClassifier(str(cascades / "haarcascade_frontalface_default.xml"))


@dataclass
class FaceBox:
    t: float
    x: float  # normalised 0..1 (top-left)
    y: float
    w: float
    h: float


@dataclass
class VisualFeatures:
    motion: np.ndarray  # per second mean abs frame difference (0..1)
    scene_cut: np.ndarray  # per second: 1 if a hard cut happened
    brightness_jump: np.ndarray  # per second abs change of mean brightness
    face_area: np.ndarray  # per second largest face area (fraction of frame), 0 = none
    faces: list[FaceBox] = field(default_factory=list)
    width: int = 0
    height: int = 0
    fps: float = 0.0


def probe_video(path: Path) -> tuple[int, int, float, float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate:format=duration", "-of", "default=nw=1", str(path)],
        capture_output=True, text=True, check=True, timeout=60,
    ).stdout
    kv = dict(line.split("=", 1) for line in out.strip().splitlines() if "=" in line)
    num, _, den = kv.get("r_frame_rate", "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    return int(kv["width"]), int(kv["height"]), fps, float(kv.get("duration", "0") or 0)


def iter_frames(path: Path, fps: float, width: int, height: int, *, start: float | None = None,
                duration: float | None = None):
    """Yield downscaled BGR frames sampled at ``fps``."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vf", f"fps={fps},scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    size = width * height * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
    finally:
        proc.stdout.close()
        proc.wait(timeout=30)


def visual_features(path: Path, *, sample_fps: float = 4.0, start: float | None = None,
                    duration: float | None = None, detect_faces: bool = True) -> VisualFeatures:
    src_w, src_h, src_fps, _ = probe_video(path)
    aw = 320
    ah = max(2, int(round(src_h * aw / src_w / 2)) * 2)
    motion, cuts, bright, face_area = [], [], [], []
    faces: list[FaceBox] = []
    prev_gray = prev_hist = None
    prev_b = None
    cascade = _face_cascade() if detect_faces else None
    for i, frame in enumerate(iter_frames(path, sample_fps, aw, ah, start=start, duration=duration)):
        t = i / sample_fps
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        hist = cv2.calcHist([frame], [0, 1, 2], None, [8, 8, 8], [0, 256] * 3)
        cv2.normalize(hist, hist)
        b = float(gray.mean()) / 255
        if prev_gray is not None:
            motion.append(float(np.mean(cv2.absdiff(gray, prev_gray))) / 255)
            assert prev_hist is not None
            corr = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)
            cuts.append(1.0 if corr < 0.55 else 0.0)
            bright.append(abs(b - (prev_b or b)))
        else:
            motion.append(0.0)
            cuts.append(0.0)
            bright.append(0.0)
        area = 0.0
        if cascade is not None and i % 2 == 0:  # faces at half the sample rate
            dets = cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(18, 18))
            for x, y, w, h in dets:
                faces.append(FaceBox(t, x / aw, y / ah, w / aw, h / ah))
                area = max(area, (w * h) / (aw * ah))
        face_area.append(area)
        prev_gray, prev_hist, prev_b = gray, hist, b

    def per_second(xs: list[float], agg=np.mean) -> np.ndarray:
        k = int(round(sample_fps))
        n = len(xs) // k
        if n == 0:
            return np.zeros(0, dtype=np.float32)
        return agg(np.asarray(xs[: n * k], dtype=np.float32).reshape(n, k), axis=1).astype(np.float32)

    return VisualFeatures(per_second(motion), per_second(cuts, np.max), per_second(bright, np.max),
                          per_second(face_area, np.max), faces, src_w, src_h, src_fps)


# ------------------------------------------------------------------------------------------- chat
LAUGH_TOKENS = {"lul", "lol", "lmao", "lmfao", "kekw", "omegalul", "icant", "😂", "🤣", "💀", "xd", "haha",
                "hahaha", "dead", "pepelaugh", "kek"}
HYPE_TOKENS = {"pog", "poggers", "pogchamp", "w", "letsgo", "lets go", "clip", "clip it", "insane", "omg",
               "wtf", "no way", "nah", "sheesh", "🔥", "monkas", "hype"}


@dataclass
class ChatSecond:
    count: int = 0
    laugh: int = 0
    hype: int = 0
    clip_calls: int = 0
    unique: int = 0


def classify_message(text: str) -> tuple[bool, bool, bool]:
    t = text.lower()
    toks = set(t.replace("!", " ").replace("?", " ").split())
    laugh = bool(toks & LAUGH_TOKENS) or "haha" in t
    hype = bool(toks & HYPE_TOKENS) or any(p in t for p in ("no way", "lets go", "let's go"))
    clip = "clip" in toks or "clip it" in t
    return laugh, hype, clip


# ------------------------------------------------------------------------------------------- stats
@dataclass
class RunningStats:
    """Exponentially weighted mean/variance: the stream's own 'normal' level."""

    alpha: float = 0.01
    mean: float | None = None
    var: float = 1.0

    def update(self, values: np.ndarray) -> None:
        for v in np.asarray(values, dtype=np.float64).ravel():
            if self.mean is None:
                self.mean, self.var = float(v), 1.0
                continue
            d = float(v) - self.mean
            self.mean += self.alpha * d
            self.var = (1 - self.alpha) * (self.var + self.alpha * d * d)

    def z(self, values: np.ndarray) -> np.ndarray:
        if self.mean is None:
            return np.zeros_like(np.asarray(values, dtype=np.float32))
        return ((np.asarray(values, dtype=np.float32) - self.mean) / max(np.sqrt(self.var), 1e-3)).astype(
            np.float32)


def robust_z(x: np.ndarray) -> np.ndarray:
    """z-score using median/MAD within a window (used when no stream baseline exists, e.g. a single clip)."""
    if len(x) == 0:
        return x
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826
    return ((x - med) / max(float(mad), 1e-3)).astype(np.float32)


def squash(z: float, center: float = 1.0, scale: float = 1.2) -> float:
    """Map a z-score to 0..1 smoothly (z=center -> 0.5)."""
    return float(1 / (1 + np.exp(-(z - center) / scale)))
