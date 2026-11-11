#!/usr/bin/env bash
# Prove that unauthenticated users cannot reach the dashboard or the API.
# Usage: WEB_DOMAIN=... API_DOMAIN=... [CF_ACCESS_CLIENT_ID=... CF_ACCESS_CLIENT_SECRET=...] ./verify-access.sh
set -uo pipefail
fail=0
check() {  # name url expected-regex [extra curl args...]
  local name=$1 url=$2 want=$3; shift 3
  local code loc
  read -r code loc < <(curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" "$@" "$url")
  if [[ "$code $loc" =~ $want ]]; then echo "PASS  $name → $code ${loc:0:60}"; else echo "FAIL  $name → $code ${loc:0:60}"; fail=1; fi
}
check "dashboard without login redirects to Access" "https://${WEB_DOMAIN}/" "^(302|303) https://[^ ]*cloudflareaccess\.com"
check "dashboard API without login is blocked" "https://${WEB_DOMAIN}/api/backend/v1/overview" "^(302|303|401|403) "
check "API without service token is blocked" "https://${API_DOMAIN}/v1/me" "^(302|303|401|403) "
check "forged Access header is rejected" "https://${WEB_DOMAIN}/api/session" "^(302|303|401|403) " -H "Cf-Access-Jwt-Assertion: forged"
if [[ -n "${CF_ACCESS_CLIENT_ID:-}" ]]; then
  check "service token reaches API health" "https://${API_DOMAIN}/healthz" "^200 " \
    -H "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" -H "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}"
  check "service token alone cannot use the API (needs Worker HMAC)" "https://${API_DOMAIN}/v1/me" "^401 " \
    -H "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" -H "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}"
fi
exit $fail
