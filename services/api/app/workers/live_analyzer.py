"""Real-time analysis of the authorized live feed (full audio/video mode)."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from pathlib import Path
from uuid import UUID

from app.config import get_settings
from app.db import session_scope
from app.detection import engine
from app.detection.context import default_analyzer
from app.detection.signals import RunningStats, audio_features, load_audio, visual_features
from app.detection.transcribe import Word, transcribe
from app.models import SourceKind, SourceStatus, StreamSession, VideoSource
from app.services import app_settings, chat, highlights
from app.services.audit import syslog
from app.sources.live_ingest import RollingBuffer

log = logging.getLogger("clipx.analyzer")


class LiveAnalyzer:
    def __init__(self, session_id: UUID, *, tick_s: float = 10.0) -> None:
        s = get_settings()
        self.session_id = session_id
        self.tick_s = tick_s
        self.buffer = RollingBuffer(s.live_ingest_url, s.work_dir / "buffer" / str(session_id),
                                    segment_seconds=s.segment_seconds, buffer_seconds=s.buffer_seconds)
        self.audio_base = RunningStats(alpha=0.005)
        self.motion_base = RunningStats(alpha=0.005)
        self.words: list[Word] = []  # rolling transcript (stream-offset seconds)
        self.transcribed_until = 0.0  # buffer seconds
        self.evaluated_peaks: list[float] = []
        self.ingest_offset = 0.0  # stream offset = buffer time + ingest_offset
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        with session_scope() as db:
            sess = db.get(StreamSession, self.session_id)
            assert sess is not None
            self.buffer.start()
            self.ingest_offset = (self.buffer.started_wallclock or time.time()) - sess.started_at.timestamp()
            sess.ingest_started_at = sess.started_at.__class__.fromtimestamp(
                self.buffer.started_wallclock or time.time(), tz=sess.started_at.tzinfo)
            syslog(db, "INFO", "analyzer", "started", "Live analysis started on the authorized feed",
                   session_id=self.session_id, ingest_offset_s=round(self.ingest_offset, 1))
        restarts = 0
        try:
            while not self._stop.is_set():
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.tick_s)
                    break
                except TimeoutError:
                    pass
                if not self.buffer.alive():
                    restarts += 1
                    self._set_source(SourceStatus.degraded, f"segmenter exited; restart #{restarts}")
                    await asyncio.sleep(min(30, 2**restarts))
                    self.buffer.start()
                    continue
                self.buffer.prune()
                try:
                    await asyncio.to_thread(self.tick)
                except Exception:
                    log.exception("analysis tick failed")
        finally:
            await asyncio.to_thread(self.flush)
            self.buffer.stop(cleanup=True)
            self._set_source(SourceStatus.available, "stream ended; buffer deleted")

    def _set_source(self, status: SourceStatus, detail: str) -> None:
        from sqlalchemy import select

        with session_scope() as db:
            row = db.scalar(select(VideoSource).where(VideoSource.kind == SourceKind.live_ingest))
            if row:
                row.status, row.detail = status, detail

    # ---------------------------------------------------------------------------------------------
    def tick(self, *, final: bool = False) -> int:
        head = self.buffer.head()
        win_start = max(0.0, head - 75.0)
        if head - win_start < 12:
            return 0
        with tempfile.TemporaryDirectory(prefix="clipx-an-") as tmp:
            media = Path(tmp) / "win.mp4"
            self.buffer.extract(win_start, head, media, reencode=False)
            samples = load_audio(media)
            af = audio_features(samples)
            vf = visual_features(media, sample_fps=4.0)
            new_from = max(win_start, self.transcribed_until - 1.0)
            if head - new_from > 1.0:
                # Whisper times are relative to the trimmed audio, so offset = absolute time of new_from.
                tr = transcribe(media, offset=new_from + self.ingest_offset, start=new_from - win_start)
                cutoff = new_from + self.ingest_offset
                self.words = [w for w in self.words if w.end <= cutoff] + list(tr.words)
                self.transcribed_until = head
            horizon = head + self.ingest_offset - 180
            self.words = [w for w in self.words if w.start >= horizon]

            t0 = win_start + self.ingest_offset
            tl = engine.Timeline(t0=t0, audio=af, visual=vf, words=self.words)
            tl.audio_z = self.audio_base.z(af.rms_db) if self.audio_base.mean is not None else None
            tl.motion_z = self.motion_base.z(vf.motion) if self.motion_base.mean is not None else None
            chat_secs = chat.window(str(self.session_id), int(t0), int(t0) + af.seconds)
            if any(c.count for c in chat_secs):
                tl.chat = chat_secs
            tl.finalize()
            self.audio_base.update(af.rms_db)
            self.motion_base.update(vf.motion)

            peaks = [p for p in engine.find_peaks(tl, edge_guard_s=0 if final else 6)
                     if all(abs(p - q) > 12 for q in self.evaluated_peaks)]
            return self._evaluate(tl, peaks, lo=t0, hi=head + self.ingest_offset)

    def _evaluate(self, tl: engine.Timeline, peaks: list[float], *, lo: float, hi: float) -> int:
        made = 0
        with session_scope() as db:
            sess = db.get(StreamSession, self.session_id)
            assert sess is not None
            cfg = app_settings.get_section(db, "processing")
            analyzer = default_analyzer(cfg.use_llm)
            for peak in peaks:
                self.evaluated_peaks.append(peak)
                res = engine.evaluate(
                    tl, peak, analyzer=analyzer, weights=cfg.weights.model_dump(), min_quality=cfg.min_quality,
                    min_confidence=cfg.min_confidence, min_clip_s=cfg.min_clip_s, max_clip_s=cfg.max_clip_s,
                    accepted_so_far=highlights.accepted_count(db, sess.id), target_max=cfg.target_clips_max,
                    existing=highlights.existing_ranges(db, sess.id), stream_title=sess.title,
                    game=sess.game_name, lo=lo, hi=hi)
                if res.status == "provisional":
                    # Full A/V mode already had every family available: a chat-only moment is a rejection.
                    res.status, res.reason = "rejected", res.reason
                cand = highlights.save_candidate(db, sess, res)
                if cand is None or res.status != "accepted":
                    continue
                with tempfile.TemporaryDirectory(prefix="clipx-cut-") as tmp:
                    out = Path(tmp) / "clip.mp4"
                    self.buffer.extract(res.start - self.ingest_offset, res.end - self.ingest_offset, out)
                    highlights.create_clip_from_buffer(db, sess, cand, out, mirror_twitch=cfg.mirror_as_twitch_clips)
                made += 1
            from app.services.streams import refresh_stats

            refresh_stats(db, sess)
        return made

    def flush(self) -> None:
        """At stream end: analyse the remaining tail without waiting for post-peak buffer."""
        try:
            self.tick(final=True)
        except Exception:
            log.exception("final flush failed")
