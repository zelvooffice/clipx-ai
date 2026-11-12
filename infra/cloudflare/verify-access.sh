#!/usr/bin/env bash
# Prove that unauthenticated users cannot reach the dashboard, its API or its media.
# Usage: WEB_DOMAIN=clipx-ai.<subdomain>.workers.dev ./verify-access.sh
set -uo pipefail
: "${WEB_DOMAIN:?}"
fail=0
check() {  # name url expected-regex [extra curl args...]
  local name=$1 url=$2 want=$3; shift 3
  local code loc
  read -r code loc < <(curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" "$@" "$url")
  if [[ "$code $loc" =~ $want ]]; then echo "PASS  $name → $code ${loc:0:60}"; else echo "FAIL  $name → $code ${loc:0:60}"; fail=1; fi
}
check "dashboard without login redirects to Access" "https://${WEB_DOMAIN}/" "^(302|303) https://[^ ]*cloudflareaccess\.com"
check "API without login is blocked" "https://${WEB_DOMAIN}/api/v1/overview" "^(302|303|401|403) "
check "media without login is blocked" "https://${WEB_DOMAIN}/api/v1/media/clips/x/render.mp4" "^(302|303|401|403) "
check "forged Access header is rejected" "https://${WEB_DOMAIN}/api/v1/session" "^(302|303|401|403) " -H "Cf-Access-Jwt-Assertion: forged"
exit $fail
