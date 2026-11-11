# CLIPX AI

Autonomous clipping and short-form publishing for **Lacy's Twitch streams**: detects when Lacy goes live, finds
genuinely interesting moments, edits them into 1080×1920 vertical videos with synced yellow subtitles, writes captions
with `#Lacy`, and sends them to TikTok through the official Content Posting API, all from a private dashboard.

```
apps/web/        Dashboard: Next.js 16 on Cloudflare Workers via vinext, behind Cloudflare Access (8 pages)
services/api/    FastAPI + Postgres + Redis + Celery: Twitch/TikTok integrations, highlight engine,
                 FFmpeg/OpenCV renderer, faster-whisper subtitles, publishing, analytics, live monitor
infra/           docker-compose processing stack, MediaMTX ingest, Cloudflare Access setup/verify scripts
docs/            ARCHITECTURE · DEPLOYMENT (incl. deploy checklist) · TESTING · ACCESSIBILITY
```

## What works today, and what needs authorization

Everything below is implemented and tested. Some features need accounts that only you can grant. Without them,
the dashboard reports exactly what is unavailable and never shows invented data.

| Capability | Needs | Without it |
|---|---|---|
| LIVE / offline detection (EventSub WebSocket + 60 s polling) | Twitch app credentials + any connected Twitch account | Status monitoring off |
| Native Twitch clips (Create Clip, 5–60 s) | Twitch account with `clips:edit` | No Twitch clips |
| Clip **media** download + precise VOD clips | **Lacy or a channel editor** authorizes `channel:manage:clips` / `editor:manage:clips` | Chat-nominated clips stay *unverified*; nothing is scraped |
| Real-time audio/video/speech analysis | The channel pushes an **authorized second stream** to the MediaMTX ingest | Chat-only mode: moments are only *nominated* by chat |
| Contextual moment analysis + captions by Claude | `ANTHROPIC_API_KEY` | Labelled heuristic analysis / template captions |
| TikTok Direct Post / inbox drafts / analytics | TikTok app (Login Kit + Content Posting) and a connected account | Clips are rendered and queued, not sent |
| Public TikTok posts | TikTok **app audit** approval | Direct Post limited to "Only me" (SELF_ONLY) |
| Viral Edits from popular content | Twitch credentials (+ optional YouTube API key) | Templates still work on CLIPX's own clips |

**How auto-publish works:** TikTok requires the creator to pick the privacy level (no default), interaction
settings and a consent line **for every Direct Post**. So CLIPX automates everything up to that point and then either:
- delivers the video as a **draft to the TikTok inbox** automatically, after a recorded one-time consent, or
- queues a **one-click confirmation card** that meets TikTok's UX rules.

It never posts silently.

## Quick start (local, ~5 minutes)

Prerequisites: Python 3.12+, Node 22, PostgreSQL 16, Redis 7, FFmpeg (with libass), `uv`.

```bash
# API
cd services/api
uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"
cp ../../.env.example .env        # for local dev set: ENVIRONMENT=development, STORAGE_BACKEND=local,
                                  # DATABASE_URL=postgresql+psycopg://clipx:clipx@localhost:5432/clipx
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --port 8000
```

```bash
# Dashboard (separate terminal)
cd apps/web
npm ci
cp .env.example .env.local        # same INTERNAL_API_SECRET as the API; DEV_AUTH_EMAIL = an AUTHORIZED_EMAILS entry
npm run dev                       # http://localhost:3000   (or: npm run dev:vinext for the Workers runtime)
```

Background workers and the live monitor (optional locally):

```bash
cd services/api
.venv/bin/celery -A app.workers.celery_app.celery worker -l INFO
.venv/bin/celery -A app.workers.celery_app.celery beat -l INFO
.venv/bin/python -m app.workers.live_monitor
```

Production setup, secrets, Cloudflare Access, R2, the tunnel and the deploy checklist are in
**[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

## Tests

```bash
cd services/api && .venv/bin/pytest -q          # real Postgres/Redis/FFmpeg/faster-whisper; external APIs are contract-tested
cd apps/web && npx tsc --noEmit && npx vitest run && npm run build:vinext
```

External API payloads used in tests are **labelled fixtures** copied from the official documentation examples
(`services/api/tests/fixtures/`). They are never shown in the product. Tests marked `integration` hit the real
Twitch/TikTok APIs and run only when credentials are provided. See [docs/TESTING.md](docs/TESTING.md).

## Security model (summary)

- Cloudflare Access (email one-time PIN + allowlist) in front of the dashboard, **plus a dashboard password asked on
  every visit (every new tab), for every user**. The lock is enforced by the API, so it can't be bypassed in the browser.
- The Worker **re-verifies** the Access JWT (issuer, audience, signature) and the allowlist, and **fails closed** if
  auth isn't configured.
- Worker → API calls are HMAC-signed (timestamp, nonce, method, path, user, body hash; ±5 min; replay-protected), and
  the API re-checks the allowlist.
- The API is reachable only through a Cloudflare Tunnel with a service-token Access policy.
- OAuth tokens are encrypted with AES-256-GCM (with key rotation). OAuth `state` is single-use and bound to the user.
- Mutations are CSRF-protected (origin check plus double-submit token), rate-limited and written to the audit log.
- No secret is ever bundled into frontend code, and `.env` files are git-ignored.

Details: [docs/ARCHITECTURE.md §4.6](docs/ARCHITECTURE.md).
