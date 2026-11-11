#!/usr/bin/env bash
# Configure Cloudflare Zero Trust Access for CLIPX AI (idempotent where possible).
# API shapes verified against developers.cloudflare.com/api (zero_trust access apps / policies /
# identity_providers / service_tokens, 2026-11-11). `destinations` replaces the deprecated `domain` field.
#
# Required env:
#   CLOUDFLARE_API_TOKEN   token with "Access: Apps and Policies Write", "Access: Organizations, Identity
#                          Providers, and Groups Write", "Access: Service Tokens Write"
#   CLOUDFLARE_ACCOUNT_ID
#   WEB_DOMAIN             dashboard hostname, e.g. clipx.example.com (custom domain of the Worker)
#   API_DOMAIN             API hostname served by the Cloudflare Tunnel, e.g. clipx-api.example.com
#   AUTHORIZED_EMAILS      comma separated allowlist
# Optional:
#   EVENTSUB_WEBHOOK=1     also create a bypass app for the Twitch EventSub webhook path (HMAC-verified)
set -euo pipefail

: "${CLOUDFLARE_API_TOKEN:?}" "${CLOUDFLARE_ACCOUNT_ID:?}" "${WEB_DOMAIN:?}" "${API_DOMAIN:?}" "${AUTHORIZED_EMAILS:?}"
API="https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/access"

cf() {  # cf METHOD PATH [JSON]
  local out
  out=$(curl -sS -X "$1" "${API}$2" -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
        -H "Content-Type: application/json" ${3:+--data "$3"})
  if [[ "$(jq -r '.success' <<<"$out")" != "true" ]]; then
    echo "Cloudflare API error on $1 $2: $(jq -c '.errors' <<<"$out")" >&2
    exit 1
  fi
  echo "$out"
}

echo "→ One-time PIN identity provider"
OTP_ID=$(cf GET /identity_providers | jq -r '.result[] | select(.type=="onetimepin") | .id' | head -n1)
if [[ -z "$OTP_ID" ]]; then
  OTP_ID=$(cf POST /identity_providers '{"name":"One-time PIN","type":"onetimepin","config":{}}' | jq -r '.result.id')
fi
echo "  id: $OTP_ID"

INCLUDE=$(jq -nc --arg e "$AUTHORIZED_EMAILS" '[$e | split(",")[] | gsub("^\\s+|\\s+$";"") | select(length>0) | {email:{email:.}}]')

existing_app() { cf GET "/apps?name=$(jq -rn --arg n "$1" '$n|@uri')" | jq -r --arg n "$1" '.result[] | select(.name==$n) | .id' | head -n1; }

echo "→ Dashboard application (${WEB_DOMAIN}) — email OTP + allowlist"
WEB_APP=$(jq -nc --arg d "$WEB_DOMAIN" --arg idp "$OTP_ID" --argjson inc "$INCLUDE" '{
  name: "CLIPX AI", type: "self_hosted", destinations: [{type: "public", uri: $d}],
  session_duration: "24h", allowed_idps: [$idp], auto_redirect_to_identity: true, app_launcher_visible: false,
  policies: [{name: "Authorized emails", decision: "allow", include: $inc, precedence: 1}]}')
ID=$(existing_app "CLIPX AI")
if [[ -n "$ID" ]]; then RES=$(cf PUT "/apps/$ID" "$WEB_APP"); else RES=$(cf POST /apps "$WEB_APP"); fi
WEB_AUD=$(jq -r '.result.aud' <<<"$RES")

echo "→ Service token for the web Worker → API calls"
TOKEN=$(cf POST /service_tokens '{"name":"clipx-web-worker","duration":"8760h"}')
TOKEN_ID=$(jq -r '.result.id' <<<"$TOKEN")
CLIENT_ID=$(jq -r '.result.client_id' <<<"$TOKEN")
CLIENT_SECRET=$(jq -r '.result.client_secret' <<<"$TOKEN")

echo "→ API application (${API_DOMAIN}) — service token only"
API_APP=$(jq -nc --arg d "$API_DOMAIN" --arg t "$TOKEN_ID" '{
  name: "CLIPX AI API", type: "self_hosted", destinations: [{type: "public", uri: $d}], session_duration: "24h",
  app_launcher_visible: false,
  policies: [{name: "Web Worker service token", decision: "non_identity", include: [{service_token: {token_id: $t}}], precedence: 1}]}')
ID=$(existing_app "CLIPX AI API")
if [[ -n "$ID" ]]; then cf PUT "/apps/$ID" "$API_APP" >/dev/null; else cf POST /apps "$API_APP" >/dev/null; fi

if [[ "${EVENTSUB_WEBHOOK:-0}" == "1" ]]; then
  echo "→ Bypass for the Twitch EventSub webhook path (every request is HMAC-verified by the API)"
  HOOK_APP=$(jq -nc --arg d "${API_DOMAIN}/webhooks/twitch/eventsub" '{
    name: "CLIPX AI EventSub webhook", type: "self_hosted", destinations: [{type: "public", uri: $d}],
    app_launcher_visible: false, policies: [{name: "Twitch", decision: "bypass", include: [{everyone: {}}], precedence: 1}]}')
  ID=$(existing_app "CLIPX AI EventSub webhook")
  if [[ -n "$ID" ]]; then cf PUT "/apps/$ID" "$HOOK_APP" >/dev/null; else cf POST /apps "$HOOK_APP" >/dev/null; fi
fi

TEAM=$(curl -sS "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/access/organizations" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" | jq -r '.result.auth_domain')

cat <<OUT

Done. Configure the web Worker with:
  CF_ACCESS_TEAM_DOMAIN=https://${TEAM}
  CF_ACCESS_AUD=${WEB_AUD}
  API_ORIGIN=https://${API_DOMAIN}
and store these as Worker secrets (shown once — they are not saved anywhere by this script):
  npx wrangler secret put CF_ACCESS_CLIENT_ID      # ${CLIENT_ID}
  npx wrangler secret put CF_ACCESS_CLIENT_SECRET  # (value printed below)
CF_ACCESS_CLIENT_SECRET=${CLIENT_SECRET}
OUT
