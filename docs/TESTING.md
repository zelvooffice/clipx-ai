# CLIPX AI — Test Strategy & Plan

## Free-plan Worker (apps/web) — what runs in CI
| Suite | What it proves |
|---|---|
| `tests/api.test.ts` | per-visit password (setup once, unlock, lockout after 5, change signs out other sessions), CSRF + Origin, browser processing protocol (claim exclusivity, render upload, unique caption with `#Lacy`, Range media), cue/edit validation, TikTok consent rules, analytics never invents numbers |
| `tests/cron.test.ts` | live/offline detection once, viewer-clip ranking (views, duplicates by overlap, threshold, per-stream max, never padded, locked clips untouched), Get Clips Download only as verified editor/broadcaster (403 → stop), streaming into R2 |
| `tests/access.test.ts` | Cloudflare Access JWT gate fails closed |

D1 runs on `node:sqlite` (same SQLite dialect) and R2 is an in-memory double (`tests/helpers/cf.ts`); Twitch responses
are labelled fixtures. The render filter graphs were validated with native FFmpeg (fit, crop, POV, compilation,
stack) and the full pipeline end-to-end in Chromium against the local Worker (`npm run dev`): unlock → background
tab claims the clip → ffmpeg.wasm render (12 s clip in 49 s) → upload → caption; POV viral edit rendered in 15 s.

## Principles
- **Real infrastructure where it is local**: tests run against real PostgreSQL (schema created by the Alembic
  migration, so migrations are tested too), real Redis, real FFmpeg/OpenCV, and real faster-whisper.
- **Contract tests for external APIs** (Twitch, TikTok, Anthropic): HTTP is intercepted with `respx`; request
  shapes are asserted against the official documentation, and responses are copied from the documentation's
  examples. Every such payload lives in `tests/fixtures/` and is labelled **FIXTURE — not real data**.
- **Real-credential integration tests** (`-m integration`) run automatically when the corresponding
  credentials are present in the environment and are skipped (never faked) otherwise.
- Fixture data is never shown in the product UI and never presented as real Twitch/TikTok data.

## Pyramid
| Layer | Scope | Speed |
|---|---|---|
| Unit | scoring, gates, dedupe, boundary snapping, subtitle grouping/sync, edit plans, chunk plans, crypto, signing, policy validation | ms |
| Integration (local infra) | DB ops + migration, job runner (idempotency, retries, leases, recovery), API endpoints with signed requests, rolling buffer with FFmpeg, full render, transcription | s |
| Contract (respx) | Twitch OAuth/Helix/EventSub (webhook + WebSocket), TikTok OAuth/creator info/Direct Post/inbox/upload/status/video list | ms |
| End-to-end (in-process) | clip media → transcribe → render → caption → publish queue → TikTok upload → status | s |
| Real credentials (`integration`) | Twitch app token, Get Users/Streams for the target channel; TikTok token validity | network |

## Coverage map (required areas → tests)
| Area | Tests |
|---|---|
| Twitch OAuth | `test_twitch.py::test_authorize_url_scopes_*`, `test_exchange_and_validate`, `test_refresh_*`, callback state checks in `test_api.py` |
| LIVE / offline detection | `test_live.py` (EventSub webhook challenge + notification, WebSocket client, poll reconcile, idempotency, finalize) |
| Create Clip + polling | `test_live.py::test_create_clip_and_poll_*`, restricted/not-live classification |
| Video source validation | `test_sources.py` (capability matrix, analysis modes, rolling buffer extract/prune) |
| Highlight scoring | `test_detection.py` (renormalisation, ≥2-family gate, loudness-only and chat-only rejection, adaptive threshold, cap) |
| Duplicate prevention | `test_detection.py::test_dedupe_*`, `test_jobs.py::test_enqueue_idempotent`, publishing near-duplicates |
| Video rendering | `test_video.py::test_render_*` (1080×1920, H.264/AAC, −14 LUFS, layouts) |
| Subtitle synchronisation | `test_video.py::test_cues_*`, `test_transcription.py` (real ASR word timing vs. known speech) |
| TikTok OAuth | `test_tiktok.py::test_token_*` (refresh-token rotation stored) |
| Publishing workflow | `test_tiktok.py` (policy, confirmation, chunked upload, status), `test_pipeline.py` |
| Authentication / access restrictions | `test_security.py` (signature, replay, skew, allowlist, Access JWT, rate limit, media path traversal) |
| Database operations | migration applied in `conftest.py`; constraints exercised across suites |
| Background jobs | `test_jobs.py` (dispatch-after-commit, retry/backoff, dead, RetryLater, lease recovery, dead hooks) |

## Running
```bash
cd services/api
pytest                      # everything that can run locally
pytest -m "not media"       # skip FFmpeg/ASR heavy tests
pytest -m integration       # real Twitch/TikTok calls (needs credentials)
```
