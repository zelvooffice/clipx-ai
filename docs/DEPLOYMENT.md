# Deploying CLIPX AI

Two deployables:

| Part | Where | How |
|---|---|---|
| Dashboard (`apps/web`, vinext) | Cloudflare Workers, behind Cloudflare Access | GitHub Actions `deploy-web.yml` on every green push to `main` |
| Processing server (`services/api` + `infra/`) | Any Linux VM (4+ vCPU, 8+ GB RAM; GPU optional) | `docker compose` |

The processing server opens **no inbound HTTP ports**: the API is published through a Cloudflare Tunnel and protected
by an Access service-token policy. Only MediaMTX (RTMP 1935 / SRT 8890, password-protected) is reachable from outside.

---

## 1. External accounts (one-time, needs a human)

| Step | Where | Result |
|---|---|---|
| Twitch app | dev.twitch.tv/console → Register application. OAuth redirect URL: `https://<WEB_DOMAIN>/api/oauth/twitch/callback` | `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` |
| TikTok app | developers.tiktok.com → Manage apps → add **Login Kit** + **Content Posting API** (Direct Post on). Redirect URI: `https://<WEB_DOMAIN>/api/oauth/tiktok/callback/`. Scopes: `user.info.basic, video.publish, video.upload, video.list` | `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` |
| TikTok audit | Submit the app for audit when ready. Until approved, Direct Post is limited to **SELF_ONLY** and private accounts (≤5 users / 24 h). Keep `client_audited=false` in Settings until approved. | public posting |
| Lacy / channel editor | Lacy or a channel editor connects in **Settings → Twitch** with the *Channel editor* / *Lacy* button. | clip media downloads + precise VOD clips |
| Authorized live feed | The channel adds a second output in OBS: `rtmp://<server>:1935/lacy?user=publisher&pass=<INGEST_PUBLISH_PASSWORD>` | real-time audio/video analysis |
| Anthropic (optional) | console.anthropic.com → API key | contextual highlight analysis + captions (`ANTHROPIC_API_KEY`) |

> Product-policy note (TikTok Content Sharing Guidelines): TikTok lists "a utility tool to help upload contents to the
> account(s) you or your team manages" as not acceptable intended use, and requires per-post creator choices for
> Direct Post. CLIPX therefore never posts silently; discuss the use case with TikTok during audit.

## 2. Cloudflare (one-time)

```bash
cd apps/web && npx wrangler login              # or export CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID
npx wrangler r2 bucket create clipx-media      # media storage
```
Create an R2 API token (Object Read & Write, bucket `clipx-media`) → `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`.

Tunnel for the API: Zero Trust → Networks → Tunnels → Create → public hostname `<API_DOMAIN>` → service
`http://api:8000` → copy the token into `CLOUDFLARE_TUNNEL_TOKEN`.

Access (email one-time PIN + allowlist, API service token, optional EventSub webhook bypass):
```bash
CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... WEB_DOMAIN=clipx.example.com \
API_DOMAIN=clipx-api.example.com AUTHORIZED_EMAILS=you@example.com infra/cloudflare/setup-access.sh
```
It prints `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD` and the service-token credentials.

Worker secrets (set once; they persist across deploys):
```bash
cd apps/web
npx wrangler secret put INTERNAL_API_SECRET        # same value as the API's INTERNAL_API_SECRET
npx wrangler secret put CF_ACCESS_CLIENT_ID
npx wrangler secret put CF_ACCESS_CLIENT_SECRET
```
Attach the custom domain `<WEB_DOMAIN>` to the `clipx-ai` Worker (Workers → clipx-ai → Settings → Domains & Routes).
With `CLIPX_DOMAIN` set, `workers.dev` and preview URLs are disabled so no hostname bypasses Access.

GitHub repository → Settings → Secrets and variables → Actions:
- Secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` (optional: `REAL_TWITCH_CLIENT_ID/SECRET` for live integration tests)
- Variables: `CLIPX_DOMAIN`, `API_ORIGIN` (`https://<API_DOMAIN>`), `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, `AUTHORIZED_EMAILS`

## 3. Processing server

```bash
git clone <repo> && cd clipx-ai
cp .env.example .env && $EDITOR .env          # fill every value marked REQUIRED
python3 -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"   # TOKEN_ENCRYPTION_KEYS
openssl rand -hex 32                                                              # INTERNAL_API_SECRET
cd infra && docker compose --env-file ../.env up -d --build
docker compose logs -f migrate api monitor
```
`migrate` runs `alembic upgrade head` before any app container starts. The API refuses to start in production with an
insecure configuration (missing secrets, no allowlist, non-R2 storage).

## 4. Deploy the dashboard

Push to `main` → CI → `Deploy dashboard to Cloudflare Workers` (also runnable manually). The workflow refuses to deploy
when Access variables are missing and finishes by running `infra/cloudflare/verify-access.sh`.

---

## Deploy Checklist: CLIPX AI — first production deployment
**Date:** ________ | **Deployer:** ________

### Pre-Deploy
- [ ] CI green on `main` (API lint/mypy/tests incl. real FFmpeg + Whisper; web tsc/vitest/next build/vinext build)
- [ ] `.env` complete on the server; `TOKEN_ENCRYPTION_KEYS` and `INTERNAL_API_SECRET` freshly generated, stored in a password manager
- [ ] `INTERNAL_API_SECRET` identical on the API and the Worker
- [ ] Twitch + TikTok redirect URIs registered exactly as shown in **Settings** (trailing slash matters for TikTok)
- [ ] `setup-access.sh` run; `AUTHORIZED_EMAILS` identical in Access, Worker vars and API env
- [ ] R2 bucket created; R2 token scoped to the bucket only
- [ ] Database backup schedule configured (`pg_dump` cron or managed Postgres); migration `0001` reviewed
- [ ] Rollback plan: previous Worker version (Workers → Deployments → Rollback); previous image tag for the server
- [ ] TikTok `client_audited` left **false** until the audit is approved

### Deploy
- [ ] `docker compose up -d --build` → `migrate` exits 0; `api` healthy; `worker`, `beat`, `monitor` running
- [ ] `curl https://<API_DOMAIN>/healthz` **without** token → blocked (302/403)
- [ ] Dashboard deploy job green, including `verify-access.sh`
- [ ] Log in via email OTP with an allowlisted address; a non-allowlisted address is refused
- [ ] Settings → Twitch: connect operator account → capabilities show *Live detection* + *Native clips*
- [ ] Live Monitor: monitor **running**, EventSub **subscribed**, poller **ok**
- [ ] Settings → TikTok: connect → creator info loads in Publishing
- [ ] Smoke: OBS test stream to the ingest → Live Monitor shows *full A/V* mode and evaluated moments

### Post-Deploy (first live stream)
- [ ] Session reaches `complete` after Lacy goes offline; buffer directory deleted
- [ ] Rendered clips play in Auto Clips; subtitles synced; no failed jobs in Settings → Failed background jobs
- [ ] One clip sent as TikTok draft / confirmed Direct Post end-to-end; status reaches *Sent to inbox* / *Published*
- [ ] Record observed render time per clip and detection latency (Live Monitor) as baselines

### Rollback Triggers
- Dashboard returns 5xx for > 2 min, or any page reachable without Access → roll back the Worker immediately
- API `/readyz` failing > 5 min, or dead jobs > 20 % of jobs in an hour → `docker compose down` the workers, investigate
- Detection latency > 60 s average or render time > 3× clip duration for a whole stream → scale workers or roll back image
- Any TikTok post published with settings different from the confirmed ones → disable auto-publish, roll back

## Operations
- Logs: `docker compose logs -f <service>`; app events are also in the dashboard (Overview → Recent activity, Live timeline).
- Failed jobs: Settings → *Failed background jobs* → Retry (safe: every job is idempotent).
- Key rotation: prepend a new key to `TOKEN_ENCRYPTION_KEYS` (comma separated) and restart; old tokens still decrypt.
- Scaling: add `worker` replicas (`docker compose up -d --scale worker=3`); for faster/larger ASR set `WHISPER_MODEL=medium`
  or run on a GPU host with `WHISPER_DEVICE=cuda` and `WHISPER_COMPUTE_TYPE=float16`.
