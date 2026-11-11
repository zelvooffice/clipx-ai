"""Authorized live video feed adapter (RTMP/SRT) with a short rolling buffer.

The channel (or its production team) pushes a copy of the stream to our MediaMTX ingest — e.g. a second
output in OBS. We never pull Twitch's player stream. FFmpeg writes 2-second MPEG-TS segments; segments
older than ``buffer_seconds`` are deleted continuously, so the full stream is never recorded.
"""

from __future__ import annotations

import csv
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("clipx.ingest")


@dataclass
class Segment:
    path: Path
    start: float  # seconds since buffer start
    end: float


class RollingBuffer:
    def __init__(self, url: str, workdir: Path, *, segment_seconds: int = 2, buffer_seconds: int = 240,
                 ffmpeg: str = "ffmpeg") -> None:
        self.url = url
        self.dir = workdir
        self.segment_seconds = segment_seconds
        self.buffer_seconds = buffer_seconds
        self.ffmpeg = ffmpeg
        self.proc: subprocess.Popen[bytes] | None = None
        self.started_wallclock: float | None = None
        self._pinned: list[tuple[float, float]] = []  # windows awaiting extraction: never prune these

    # -- lifecycle -------------------------------------------------------------------------
    def start(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-rw_timeout", "15000000", "-i", self.url,
            "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
            "-f", "segment", "-segment_time", str(self.segment_seconds), "-segment_format", "mpegts",
            "-segment_list", str(self.dir / "list.csv"), "-segment_list_type", "csv",
            "-segment_list_flags", "+live", "-reset_timestamps", "0",
            str(self.dir / "seg_%08d.ts"),
        ]
        log.info("starting ingest segmenter for %s", _redact(self.url))
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.started_wallclock = time.time()

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, *, cleanup: bool = True) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if cleanup:
            shutil.rmtree(self.dir, ignore_errors=True)

    # -- segments --------------------------------------------------------------------------
    def segments(self) -> list[Segment]:
        lst = self.dir / "list.csv"
        if not lst.exists():
            return []
        out = []
        with lst.open(newline="") as f:
            for row in csv.reader(f):
                if len(row) < 3:
                    continue
                p = self.dir / row[0]
                if p.exists():
                    out.append(Segment(p, float(row[1]), float(row[2])))
        return out

    def head(self) -> float:
        """Latest buffered time (seconds since buffer start)."""
        segs = self.segments()
        return segs[-1].end if segs else 0.0

    def pin(self, start: float, end: float) -> None:
        self._pinned.append((start, end))

    def unpin(self, start: float, end: float) -> None:
        if (start, end) in self._pinned:
            self._pinned.remove((start, end))

    def prune(self) -> int:
        segs = self.segments()
        if not segs:
            return 0
        horizon = segs[-1].end - self.buffer_seconds
        if self._pinned:
            horizon = min(horizon, min(s for s, _ in self._pinned) - self.segment_seconds)
        removed = 0
        for s in segs[:-1]:
            if s.end < horizon:
                s.path.unlink(missing_ok=True)
                removed += 1
        return removed

    def extract(self, start: float, end: float, out: Path, *, reencode: bool = True) -> Path:
        """Cut [start, end] (buffer seconds) into ``out``. Raises if the window is no longer buffered."""
        segs = [s for s in self.segments() if s.end > start and s.start < end]
        if not segs or segs[0].start > start + self.segment_seconds:
            raise LookupError("requested window is no longer in the rolling buffer")
        concat = out.with_suffix(".concat.txt")
        concat.write_text("".join(f"file '{s.path.as_posix()}'\n" for s in segs))
        offset = max(0.0, start - segs[0].start)
        codec = (["-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-c:a", "aac", "-b:a", "192k"]
                 if reencode else ["-c", "copy"])
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
               "-i", str(concat), "-ss", f"{offset:.3f}", "-t", f"{end - start:.3f}", *codec,
               "-movflags", "+faststart", str(out)]
        subprocess.run(cmd, check=True, timeout=300)
        concat.unlink(missing_ok=True)
        return out


def probe_url(url: str, timeout_s: int = 12) -> tuple[bool, str]:
    """Check whether the authorized feed is currently publishing."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-rw_timeout", str(timeout_s * 1_000_000), "-show_entries",
             "stream=codec_type,width,height", "-of", "csv=p=0", url],
            capture_output=True, text=True, timeout=timeout_s + 5,
        )
    except subprocess.TimeoutExpired:
        return False, "no data from ingest within timeout"
    if r.returncode != 0:
        return False, (r.stderr.strip() or "ffprobe failed")[:300]
    return ("video" in r.stdout), r.stdout.strip().replace("\n", "; ")


def _redact(url: str) -> str:
    # Stream keys live in the path; do not log them.
    head, _, _ = url.rpartition("/")
    return f"{head}/***" if head else "***"
