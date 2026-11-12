# CLIPX AI

Private clipping and short-form publishing for **Lacy's Twitch streams**. It watches for Lacy going live, picks
the moments viewers clip most, edits them into 1080×1920 vertical videos with synced yellow subtitles, writes
captions with `#Lacy`, and sends them to TikTok through the official Content Posting API. Everything runs from a
password-protected dashboard.

**It runs entirely on Cloudflare's free plan.** One Worker serves the site, the API and a cron job, backed by
D1 (database), R2 (video storage) and Workers AI (subtitles). The code lives on GitHub. Nothing else is needed.

```
apps/web/          The product: Next.js 16 dashboard on Cloudflare Workers (vinext) + Worker API + cron
  worker.ts        Worker entry: /api/v1/* and OAuth callbacks → lib/server, pages → vinext, cron → lib/server/cron
  lib/server/      D1 schema, Twitch, TikTok, captions, scheduler, per-visit password, audit log
  lib/render/      In-browser editor: ffmpeg.wasm filter graphs, subtitle (ASS) builder, edit plan
services/api/      Optional paid upgrade: FastAPI processing server with real-time A/V analysis (not used on the free plan)
infra/cloudflare/  Access (Zero Trust) setup + verification scripts
docs/              DEPLOYMENT (Bulgarian, step by step) · ARCHITECTURE · TESTING · ACCESSIBILITY
```

## How it works on the free plan

| Step | How | Notes |
|---|---|---|
| LIVE detection | Cron every minute → Twitch Helix `Get Streams` | Official API, app token |
| Finding moments | Clips Lacy's **viewers** create on Twitch (`Get Clips`), ranked by views; overlapping clips are deduplicated; up to *N* per stream (default 25), **never padded** | No real-time audio/video analysis on the free plan |
| Getting the video | Twitch `Get Clips Download`, only for **Lacy or a channel editor** who connected their account | Nothing is scraped. Without that authorization, clips are listed but not edited |
| Subtitles | Workers AI `whisper-large-v3-turbo` with word timestamps → 2–4 word yellow cues | Free tier: 10,000 neurons/day ≈ 200+ min of audio |
| Editing | **In the browser** with ffmpeg.wasm: blurred-background "fit" or "crop", punch-ins, loudness normalization, H.264/AAC 30 fps | An open, unlocked dashboard tab edits waiting clips (≈4× clip length) |
| Captions | Template captions, unique per clip, `#Lacy` first | |
| TikTok | Official OAuth + Direct Post (one-click confirmation card that follows TikTok's UX rules) or inbox drafts (automatic after recorded consent) | Unaudited TikTok apps can only post privately (SELF_ONLY) |
| Analytics | TikTok `video/list` every 3 h. Posting-time advice only after ≥10 measured posts | No invented numbers |
| Security | Cloudflare Access (email one-time PIN + allowlist), **plus a dashboard password on every visit**, CSRF, audit log, AES-GCM-encrypted OAuth tokens | Fails closed when Access isn't configured |

## Develop

```bash
cd apps/web
npm ci
cp .dev.vars.example .dev.vars   # local secrets for the Worker (never committed)
npm run dev                      # http://localhost:3001: Worker + local D1/R2, DEV_AUTH_EMAIL instead of Access
npm test                         # unit + API tests (D1 on node:sqlite, mocked Twitch/TikTok)
npm run build
```

Deployment, step by step (in Bulgarian): **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.
