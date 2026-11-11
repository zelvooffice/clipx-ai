"""Chat-only mode: chat spikes *nominate* moments; they are never accepted on chat alone.

Without an authorized video feed the only real-time signal is chat. A spike creates a *provisional*
candidate and a native Twitch clip (Create Clip captures the ~90 s before the request, so the moment the
chat reacted to is inside it). The clip is accepted only after its media is obtained through an
authorized method and passes full multi-signal analysis (see handlers.verify_clip). If media cannot be
obtained, the clip stays "unverified — chat signal only" and is never auto-published.
"""

from __future__ import annotations

import asyncio
import logging
import time
from uuid import UUID

import numpy as np

from app.db import session_scope
from app.detection import scoring
from app.detection.context import ContextVerdict
from app.detection.engine import MomentResult
from app.detection.signals import RunningStats, squash
from app.models import StreamSession
from app.services import app_settings, chat, highlights

log = logging.getLogger("clipx.chatmode")


class ChatSpikeDetector:
    def __init__(self, session_id: UUID, *, tick_s: float = 5.0, cooldown_s: float = 45.0) -> None:
        self.session_id = session_id
        self.tick_s = tick_s
        self.cooldown_s = cooldown_s
        self.base = RunningStats(alpha=0.02)
        self.last_nomination = 0.0
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_s)
                break
            except TimeoutError:
                pass
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                log.exception("chat tick failed")

    def tick(self, now_offset: float | None = None) -> bool:
        with session_scope() as db:
            sess = db.get(StreamSession, self.session_id)
            if sess is None:
                return False
            now = now_offset if now_offset is not None else time.time() - sess.started_at.timestamp()
            secs = chat.window(str(self.session_id), int(now) - 10, int(now))
            rate = float(sum(c.count for c in secs)) / 10.0
            z = float(self.base.z(np.array([rate]))[0]) if self.base.mean is not None else 0.0
            self.base.update(np.array([rate]))
            total = sum(c.count for c in secs)
            reaction = (sum(c.laugh + c.hype for c in secs) + 2 * sum(c.clip_calls for c in secs)) / max(1, total)
            if not (z > 2.5 and total >= 8 and reaction >= 0.2):
                return False
            if now - self.last_nomination < self.cooldown_s:
                return False
            cfg = app_settings.get_section(db, "processing")
            if highlights.accepted_count(db, sess.id) >= cfg.target_clips_max:
                return False
            self.last_nomination = now
            laugh_ratio = sum(c.laugh for c in secs) / max(1, total)
            category = "funny" if laugh_ratio >= 0.2 else "reaction"
            audience = 0.6 * squash(z, 1.5, 1.0) + 0.4 * min(1.0, reaction * 2)
            comps = scoring.ComponentScores(audience_reaction=audience)
            res = scoring.combine(comps, cfg.weights.model_dump(), analyzer_confidence=0.3)
            start, end = max(0.0, now - 40.0), now
            verdict = ContextVerdict(
                is_highlight=True, category=category, title="Chat-nominated moment (unverified)",
                explanation=(f"Chat activity spiked (z={z:.1f}, {total} msgs/10s, reaction ratio {reaction:.2f}). "
                             "No video signal is available, so this moment is unverified until the clip media "
                             "can be analysed."),
                context_summary="", storytelling_score=None, engagement_score=0.0, suggested_start=start,
                suggested_end=end, confidence=0.3, analyzer="chat_only")
            moment = MomentResult(now - 8, start, end, verdict, comps, res, "provisional",
                                  "chat signal only; awaiting media verification", False, "",
                                  {"chat_z": round(z, 2), "chat_msgs": total, "reaction_ratio": round(reaction, 2)}, 0)
            cand = highlights.save_candidate(db, sess, moment)
            if cand is None:
                return False
            highlights.create_provisional_clip(db, sess, cand)
            return True
