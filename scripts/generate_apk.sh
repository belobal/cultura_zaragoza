#!/usr/bin/env bash
# Generate an Android package zip via PWABuilder CloudAPK.
# Usage: ./scripts/generate_apk.sh https://your-app.onrender.com
set -euo pipefail

HOST="${1:-}"
if [[ -z "$HOST" ]]; then
  echo "Usage: $0 https://your-app.onrender.com" >&2
  exit 1
fi

HOST="${HOST%/}"
if [[ ! "$HOST" =~ ^https:// ]]; then
  echo "Host must be https://..." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$ROOT/android-apk"
mkdir -p "$OUT_DIR"
OUT_ZIP="$OUT_DIR/cultura-zaragoza-android.zip"

MANIFEST_URL="$HOST/manifest.webmanifest"
ICON_URL="$HOST/static/icons/icon-512.png"
MASK_URL="$HOST/static/icons/icon-maskable-512.png"
API="https://pwabuilder-cloudapk.azurewebsites.net/generateAppPackage"

echo "Checking PWA at $HOST ..."
wait_ok() {
  local url="$1"
  local i
  for i in $(seq 1 20); do
    if curl -fsS --max-time 60 "$url" >/dev/null; then
      return 0
    fi
    sleep 3
  done
  echo "Timeout waiting for $url" >&2
  return 1
}
wait_ok "$HOST/healthz"
wait_ok "$MANIFEST_URL"
wait_ok "$ICON_URL"

# Default: unsigned package (fine for local install testing).
# For a signed package, set APK_SIGNING_MODE=new and the APK_KEY_* env vars.
SIGNING_MODE="${APK_SIGNING_MODE:-none}"
if [[ "$SIGNING_MODE" == "new" ]]; then
  SIGNING_JSON=$(cat <<EOF
  "signingMode": "new",
  "signing": {
    "fullName": "${APK_KEY_NAME:-Cultura Zaragoza}",
    "organization": "${APK_KEY_ORG:-Cultura Zaragoza}",
    "organizationalUnit": "Mobile",
    "countryCode": "ES",
    "alias": "${APK_KEY_ALIAS:-cultura}",
    "keyPassword": "${APK_KEY_PASSWORD:?Set APK_KEY_PASSWORD}",
    "storePassword": "${APK_STORE_PASSWORD:?Set APK_STORE_PASSWORD}"
  }
EOF
)
else
  SIGNING_JSON='"signingMode": "none", "signing": null'
fi

PAYLOAD=$(cat <<EOF
{
  "packageId": "com.cultura.zaragoza",
  "name": "Cultura Zaragoza",
  "launcherName": "Cultura ZGZ",
  "host": "$HOST",
  "startUrl": "/m",
  "webManifestUrl": "$MANIFEST_URL",
  "iconUrl": "$ICON_URL",
  "maskableIconUrl": "$MASK_URL",
  "themeColor": "#8b1e2f",
  "themeColorDark": "#6f1524",
  "navigationColor": "#8b1e2f",
  "navigationColorDark": "#6f1524",
  "navigationDividerColor": "#8b1e2f",
  "navigationDividerColorDark": "#6f1524",
  "backgroundColor": "#f7f2ee",
  "display": "standalone",
  "orientation": "default",
  "appVersion": "1.0.0",
  "appVersionCode": 1,
  $SIGNING_JSON,
  "includeSourceCode": false,
  "isChromeOSOnly": false,
  "fallbackType": "customtabs",
  "enableNotifications": false,
  "enableSiteSettingsShortcut": true,
  "splashScreenFadeOutDuration": 300,
  "features": {
    "locationDelegation": { "enabled": false },
    "playBilling": { "enabled": false }
  },
  "shortcuts": [],
  "additionalTrustedOrigins": []
}
EOF
)

echo "Requesting package from PWABuilder CloudAPK ..."
HTTP_CODE=$(curl -sS -o "$OUT_ZIP" -w "%{http_code}" \
  -X POST "$API" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  --max-time 300)

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "PWABuilder returned HTTP $HTTP_CODE" >&2
  head -c 2000 "$OUT_ZIP" >&2 || true
  echo >&2
  exit 1
fi

if ! unzip -t "$OUT_ZIP" >/dev/null 2>&1; then
  echo "Response is not a zip; saving as text for inspection." >&2
  mv "$OUT_ZIP" "$OUT_DIR/pwabuilder-response.txt"
  head -c 2000 "$OUT_DIR/pwabuilder-response.txt" >&2
  exit 1
fi

unzip -l "$OUT_ZIP"
echo "Saved: $OUT_ZIP"
echo "Extract with: unzip -o '$OUT_ZIP' -d '$OUT_DIR'"
echo "Next: copy assetlinks.json to static/.well-known/ and redeploy."
