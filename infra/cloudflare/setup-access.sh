#!/usr/bin/env bash
# Put CLIPX AI behind Cloudflare Zero Trust Access: email one-time PIN + an allowlist of emails (idempotent).
# API shapes from developers.cloudflare.com/api (zero_trust access apps / identity_providers / organizations).
# `destinations` replaces the deprecated `domain` field. Works for a custom domain or the Worker's workers.dev host.
#
# Required env:
#   CLOUDFLARE_API_TOKEN   token with "Access: Apps and Policies Edit" and "Access: Organizations, Identity
#                          Providers, and Groups Edit"
#   CLOUDFLARE_ACCOUNT_ID
#   WEB_DOMAIN             dashboard hostname, e.g. clipx-ai.<subdomain>.workers.dev or clipx.example.com
#   AUTHORIZED_EMAILS      comma separated allowlist
# Prints CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD for the Worker's configuration.
set -euo pipefail

: "${CLOUDFLARE_API_TOKEN:?}" "${CLOUDFLARE_ACCOUNT_ID:?}" "${WEB_DOMAIN:?}" "${AUTHORIZED_EMAILS:?}"
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

echo "→ Zero Trust organization"
TEAM=$(cf GET /organizations | jq -r '.result.auth_domain // empty')
if [[ -z "$TEAM" ]]; then
  echo "No Zero Trust organization yet. Open dash.cloudflare.com → Zero Trust once, choose a team name and the Free plan, then re-run." >&2
  exit 1
fi
echo "  team: $TEAM"

echo "→ One-time PIN identity provider"
OTP_ID=$(cf GET /identity_providers | jq -r '.result[] | select(.type=="onetimepin") | .id' | head -n1)
if [[ -z "$OTP_ID" ]]; then
  OTP_ID=$(cf POST /identity_providers '{"name":"One-time PIN","type":"onetimepin","config":{}}' | jq -r '.result.id')
fi

INCLUDE=$(jq -nc --arg e "$AUTHORIZED_EMAILS" '[$e | split(",")[] | gsub("^\\s+|\\s+$";"") | select(length>0) | {email:{email:.}}]')
existing_app() { cf GET "/apps" | jq -r --arg n "$1" '.result[] | select(.name==$n) | .id' | head -n1; }

echo "→ Application ${WEB_DOMAIN} — email OTP + allowlist"
WEB_APP=$(jq -nc --arg d "$WEB_DOMAIN" --arg idp "$OTP_ID" --argjson inc "$INCLUDE" '{
  name: "CLIPX AI", type: "self_hosted", destinations: [{type: "public", uri: $d}],
  session_duration: "24h", allowed_idps: [$idp], auto_redirect_to_identity: true, app_launcher_visible: false,
  policies: [{name: "Authorized emails", decision: "allow", include: $inc, precedence: 1}]}')
ID=$(existing_app "CLIPX AI")
if [[ -n "$ID" ]]; then RES=$(cf PUT "/apps/$ID" "$WEB_APP"); else RES=$(cf POST /apps "$WEB_APP"); fi
WEB_AUD=$(jq -r '.result.aud' <<<"$RES")

cat <<OUT

Done. Configure the Worker with:
CF_ACCESS_TEAM_DOMAIN=https://${TEAM}
CF_ACCESS_AUD=${WEB_AUD}
OUT
