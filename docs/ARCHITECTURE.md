# CLIPX AI — System Design

> **Deployed architecture (free plan, 2026-11-12):** a single Cloudflare Worker (`apps/web/worker.ts`) serves
> the dashboard, the JSON API (`/api/v1/*`, `lib/server/api.ts`), OAuth callbacks and a per-minute Cron Trigger
> (`lib/server/cron.ts`). Storage: D1 (self-migrating schema, `lib/server/schema.ts`) and R2 (`clips/<id>/source.mp4`,
> `render.mp4`, `thumb.jpg`). Subtitles: Workers AI Whisper (word timestamps). Rendering happens in the operator's
> browser with ffmpeg.wasm (`lib/render/*`): an open dashboard tab claims a `media_ready` clip (exclusive, 15 min lease),
> renders 1080×1920 H.264/AAC and uploads it, then the Worker writes the caption and (with consent) prepares the TikTok job.
> Moments are the viewers' Twitch clips ranked by views with IoU>0.3 de-duplication, capped per stream and never padded.
> The 10 ms CPU limit of the free plan drives the design: media is always streamed (FixedLengthStream R2⇄fetch),
> PBKDF2 is 25k iterations, each cron step is bounded. The sections below describe the original full design,
> whose processing server (`services/api`) remains available as an optional paid upgrade for real-time A/V analysis.

Status: implemented (v0.1). This document is the source of truth for how the system is put together and
why. Facts about external APIs were verified against the official Twitch, TikTok and Cloudflare
documentation on 2026-11-11 (see "Verified platform constraints").

## 1. Requirements

### Functional
1. Detect when Lacy's Twitch channel goes live / offline.
2. Find genuinely interesting moments (context, visuals, speech, audience) — target 15–25 per stream,
   never padded to reach the quota.
3. Create clips (native Twitch clips and/or from an authorized live feed).
4. Edit automatically into 1080×1920 H.264 MP4 with content-aware reframing, zooms, sound design,
   loudness normalization and burned-in yellow subtitles (editable tracks kept).
5. Generate a unique caption + relevant hashtags (always `#Lacy`).
6. Publish to TikTok through the official Content Posting API with the least manual work TikTok permits.
7. Viral Edits: discover popular Lacy content from permitted sources and build template edits only from
   media we have rights to.
8. Analytics from real TikTok metrics; evidence-based posting-time recommendations.
9. Private dashboard (8 pages) protected by Cloudflare Access email OTP + allowlist.

### Non-functional
| Concern | Target |
|---|---|
| Detection latency (authorized feed) | candidate ≤ 30 s after the moment ends |
| Render time | ≤ 2× clip duration on 4 vCPU (CPU-only, `veryfast` preset) |
| Durability | a rendered clip is never lost once its job reports success (object stored before DB commit) |
| Crash recovery | stale jobs re-queued automatically within 2 min (lease expiry) |
| Scale | 1 channel, ~1 stream/day, ≤ 40 candidates/stream → tiny; optimise for correctness, not throughput |
| Security | no secrets in frontend, tokens AES-256-GCM encrypted at rest, fail-closed auth |

### Constraints
- One creator (Lacy), one operator team, solo-maintainable codebase.
- Cloudflare Workers cannot run FFmpeg/GPU work → separate processing server.
- Only official APIs. No scraping, no bypassing clip restrictions or TikTok UX rules.

## 2. Verified platform constraints (drive the design)

| Platform fact (official docs) | Design consequence |
|---|---|
| `POST /helix/clips` captures ~90 s ending "now"; params `broadcaster_id`, `title`, `duration` (5–60 s, default 30); scope `clips:edit`; async — confirm via Get Clips, assume failure after 60 s; 403 if clips restricted/disabled, 404 if not live | Native clip adapter: create → poll ≤ 60 s → mark failed. Restrictions surfaced, never retried around. |
| `GET /helix/clips/downloads` returns `landscape_download_url`/`portrait_download_url`; needs `editor:manage:clips` or `channel:manage:clips`; caller must be the broadcaster or an **editor** | The only authorized way to get clip media. Without editor/broadcaster auth the UI says "media unavailable" — we never scrape. |
| `POST /helix/videos/clips` (Create Clip From VOD): `vod_id`, `vod_offset` (end), `duration` 5–60, `title`; works on the live stream's in-progress VOD; editor/broadcaster scopes | Precise clipping of detected windows when editor auth exists. |
| Twitch does **not** provide a continuous video feed via API | Separate "authorized live feed" adapter (RTMP/SRT pushed by the channel to our MediaMTX ingest). |
| EventSub WebSocket: `wss://eventsub.wss.twitch.tv/ws`, must subscribe within 10 s of welcome, **user** access tokens, keepalive/reconnect messages; webhooks use app tokens + HMAC-SHA256(id+timestamp+body) | Default transport = WebSocket (no public endpoint needed). Webhook transport implemented and signature-verified for operators who prefer it. Polling Get Streams every 60 s reconciles missed events. |
| `channel.chat.message` v1 needs `user:read:chat`, condition `broadcaster_user_id` + `user_id` | Chat velocity is an *audience* signal only — never the sole signal. |
| TikTok Direct Post: must query creator info, show creator nickname, **manual privacy selection with no default**, interaction toggles unchecked by default, commercial disclosure, consent text "By posting, you agree to TikTok's Music Usage Confirmation" | Silent auto-posting would violate the guidelines. Direct Post always goes through a compliant one-click confirmation card. |
| Unaudited clients: SELF_ONLY, private accounts, ≤ 5 users / 24 h; ~15 posts/creator/day shared cap; 6 init requests/min | Posting caps and cooldowns enforced locally; audit status is an explicit setting. |
| Inbox upload `/v2/post/publish/inbox/video/init/` (scope `video.upload`) sends a draft the creator finishes in the TikTok app | "Auto-publish" = fully automatic preparation + automatic draft delivery (compliant automation); final tap happens in TikTok. |
| Server-held media should use `PULL_FROM_URL` from a verified domain/URL prefix; chunk rules for FILE_UPLOAD (5–64 MB chunks, <5 MB whole, sequential) | Prefer PULL_FROM_URL from the R2 custom domain; FILE_UPLOAD with spec-compliant chunking as fallback. |
| No watermarks/branding on shared content | Renderer never adds CLIPX branding. |
| Cloudflare recommends **vinext** for Next.js on Workers; Access JWT in `Cf-Access-Jwt-Assertion`, verify with `jose` against `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`, issuer = team domain, audience = AUD tag | Web app built with vinext; middleware verifies the JWT itself (defence in depth) and fails closed. |

## 3. High-level design

```
                     ┌───────────────────────── Cloudflare ─────────────────────────┐
 Browser ──HTTPS──▶  │ Access (email OTP + allowlist)                                │
                     │   └─▶ Worker: vinext/Next.js dashboard                        │
                     │         • middleware: verify Access JWT (jose) — fail closed  │
                     │         • /api/backend/* proxy: HMAC-signs every request      │
                     │         • /api/oauth/* : OAuth callbacks → backend            │
                     │ R2 bucket (clips, renders, thumbnails)  ◀──── S3 API ────┐   │
                     └──────────────────────────────┬────────────────────────────┼───┘
                                                    │ Cloudflare Tunnel           │
                     ┌──────────── Processing server (Docker Compose) ────────────┼───┐
                     │ api (FastAPI) ── Postgres ── Redis (broker, locks, chat)   │   │
                     │ live-monitor: EventSub WS + poller + chat aggregator       │   │
                     │      └─ live analyzer: FFmpeg segmenter → rolling buffer   │   │
                     │ worker (Celery): clip, media, transcribe, render, caption, ┘   │
                     │                  publish, analytics, discovery             │
                     │ beat: periodic recovery / analytics / discovery            │
                     │ mediamtx: RTMP/SRT ingest for the authorized live feed     │
                     └────────────────────────────────────────────────────────────────┘
        Twitch Helix / EventSub ◀──────┘   TikTok Content Posting API ◀──┘   Claude API (optional)
```

### Live data flow
```
stream.online ─▶ StreamSession(live) ─▶ capability probe (which sources/signals exist)
   ├─ authorized feed present ─▶ segmenter (2 s TS segments, rolling 240 s) ─▶ analyzer every 10 s:
   │     audio features + visual features + faster-whisper words + chat velocity
   │     ─▶ peak finding ─▶ context analysis (Claude or heuristic) ─▶ boundary snapping to sentences
   │     ─▶ scoring + dedupe ─▶ HighlightCandidate(accepted)
   │     ─▶ extract window from buffer (before it rolls off) ─▶ Clip(media_source=live_buffer)
   │     ─▶ optional mirror as Twitch clip (VOD clip if editor auth, else Create Clip)
   └─ no feed ─▶ chat spikes only ─▶ *provisional* candidate ─▶ Twitch clip
         ─▶ Get Clips Download (editor auth) ─▶ full post-hoc analysis on the media ─▶ accept/reject
         ─▶ no media rights ─▶ clip stays "unverified — chat signal only", never auto-published
Clip ─▶ transcribe ─▶ edit plan ─▶ render 1080×1920 ─▶ caption ─▶ publishing policy
     ─▶ PublishingJob (inbox draft automatically | awaiting_confirmation for Direct Post)
stream.offline ─▶ session finalizing ─▶ drain jobs ─▶ delete buffer/temp ─▶ stats ─▶ complete
```

## 4. Deep dive

### 4.1 Data model (PostgreSQL, Alembic-managed)
`users`, `authorized_emails`, `oauth_states`, `oauth_tokens` (encrypted), `twitch_accounts`,
`tiktok_accounts`, `stream_sessions`, `video_sources`, `highlight_candidates`, `clips`,
`subtitle_tracks`, `edit_configs`, `viral_source_videos`, `viral_edits`, `publishing_jobs`,
`publishing_history`, `analytics_snapshots`, `processing_jobs`, `audit_logs`, `system_logs`,
`app_settings`. Key invariants:
- `processing_jobs.idempotency_key` UNIQUE → the same work is never enqueued twice.
- `clips.twitch_clip_id` UNIQUE, `highlight_candidates(session_id, dedupe_key)` UNIQUE.
- `publishing_jobs(clip_id, tiktok_account_id)` UNIQUE where status not in (failed, cancelled) →
  a clip cannot be posted twice to the same account.
- All timestamps UTC `timestamptz`; JSON in `jsonb`.

### 4.2 API surface (FastAPI, `/v1`)
`GET /me`, `/overview`, `/live/status`, `/live/timeline`, `/sources`, `/clips` (+ `/{id}`, `/render`,
`/subtitles`, `/edit-config`, `/caption`), `/viral/sources`, `/viral/templates`, `/viral/edits`,
`/publishing/*` (account, creator-info, settings, queue, confirm, history), `/analytics/*`,
`/settings/*`, `/integrations/twitch/*`, `/integrations/tiktok/*`, `/audit`.
Unauthenticated (signature-verified): `POST /webhooks/twitch/eventsub`. Health: `/healthz`, `/readyz`.

### 4.3 Queue & job design
- Celery (Redis broker) with `acks_late=True`, `task_reject_on_worker_lost=True`, prefetch 1.
- Every task is wrapped by `JobRunner`: claims a `processing_jobs` row with a lease
  (`locked_until`), runs, writes the result, releases. Lease expiry → `recover_stale_jobs` (beat, 60 s)
  re-queues. Exponential backoff, `max_attempts`, then `dead` (visible in UI).
- Media pipeline writes the object to storage **before** committing the DB pointer; a crash between the
  two leaves an orphan object (cleaned up), never a dangling pointer.
- Distributed locks (Redis `SET NX PX`) guard: one live monitor, one analyzer per session, one render
  per clip.

### 4.4 Highlight scoring
Five components, weights configurable (default 30/25/15/15/15): storytelling & context, visual interest,
audio & speech, audience reaction, short-form engagement potential. Each 0–1. Missing signals are
**excluded and the weights renormalised**; confidence is reduced by missing coverage and by
disagreement between signals. Gates: quality ≥ threshold, confidence ≥ threshold, and **≥ 2
independent signal families** must support the moment (loudness alone or chat alone can never accept a
clip). Dedupe: time-overlap IoU and transcript shingle similarity. Quota: adaptive threshold may only
*rise* as the count approaches the cap; it never drops to manufacture clips.

### 4.5 Rendering
Analysis pass (5 fps, Haar faces, motion, scene cuts) → layout decision per content
(`track` face-follow / `stacked` facecam-over-gameplay / `fit` blurred background) → smoothed crop
path with dead-zone → edit plan (punch-ins on emphasis words and reaction peaks, intensity from
category, seeded per clip so no two edits are identical) → frame pipeline (ffmpeg decode → numpy/OpenCV
compose → ffmpeg encode libx264 + `ass` subtitles) → audio: loudnorm (I=-14 LUFS, TP=-1.5) +
synthesized subtle SFX at punch-ins (no third-party assets).

### 4.6 Security
Cloudflare Access (OTP, allowlist) → Worker verifies JWT (issuer + audience + signature) and checks the
allowlist again → proxies with HMAC signature (timestamp, method, path, email, body hash; ±300 s,
replay cache) → API re-checks the email against `authorized_emails`. Tokens AES-256-GCM with key
rotation. OAuth `state` stored server-side with creator email + 10-min expiry. Redis rate limiting per
email. Double-submit CSRF token + Origin check on mutating requests. Audit log for every mutation.

## 5. Scale & reliability
Load is tiny (one channel). The bottleneck is CPU for transcription/rendering: one 4-vCPU box renders
~25 clips/stream comfortably. Scale path: add Celery workers on the `render` queue (stateless, read/write
R2); GPU box for faster-whisper `large-v3` if accuracy needs it. Monitoring: `/readyz` checks DB, Redis,
storage; `system_logs` + Celery events; dead jobs surfaced in the dashboard.

## 6. Trade-offs (explicit)
| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| EventSub transport | WebSocket | Webhook | No public inbound endpoint; fewer secrets. Webhook kept for multi-instance deployments. |
| Video signal | Authorized RTMP/SRT push | Pulling the public HLS playlist | Pulling the player stream would bypass Twitch's intended access path — not permitted. |
| Contextual understanding | Claude (optional) + deterministic heuristic fallback | Heuristic only | Context needs language understanding; the fallback keeps the system working (with lower confidence) without a key. |
| Subtitles | faster-whisper word timestamps → ASS burned in + editable JSON track | SRT only | ASS gives styling + per-word emphasis; JSON keeps edits lossless. |
| Rendering | Python frame pipeline | Pure ffmpeg filtergraphs | Per-frame face-tracking crop paths are far simpler and more controllable in numpy. ~2× realtime on CPU is acceptable. |
| TikTok automation | Auto draft (inbox) + 1-click compliant Direct Post | Silent Direct Post | Silent posting violates TikTok's mandatory UX rules. |
| Frontend runtime | vinext on Workers | OpenNext | Cloudflare's current default recommendation; vinext is beta — fallback documented. |

## 7. Revisit as the system grows
- Multi-creator support (currently a single `twitch_target_login`).
- GPU workers + larger Whisper model; speaker diarization for multi-person streams.
- Replace Haar cascades with a DNN face/landmark model for expression-level visual scoring.
- Retention metrics are not exposed by the TikTok API today; revisit if TikTok adds them.
- If TikTok approves an automated Direct Post flow in writing, the single gate in
  `app/publishing/policy.py::direct_post_requires_confirmation` is the only place to change.
