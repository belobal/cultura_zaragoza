#!/usr/bin/env bash
# Generate an Android package zip via PWABuilder's packaging API.
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

echo "Checking PWA at $HOST ..."
curl -fsS "$HOST/healthz" >/dev/null
curl -fsS "$MANIFEST_URL" >/dev/null
curl -fsS "$ICON_URL" >/dev/null

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
  "signingMode": "new",
  "signing": {
    "fullName": "Cultura Zaragoza",
    "organization": "Cultura Zaragoza",
    "organizationalUnit": "Mobile",
    "countryCode": "ES"
  },
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

API="https://android.pwabuilder.com/generateAppPackage"
echo "Requesting package from PWABuilder ..."
HTTP_CODE=$(curl -sS -o "$OUT_ZIP" -w "%{http_code}" \
  -X POST "$API" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "PWABuilder returned HTTP $HTTP_CODE" >&2
  head -c 2000 "$OUT_ZIP" >&2 || true
  echo >&2
  exit 1
fi

# Detect zip magic
if ! unzip -t "$OUT_ZIP" >/dev/null 2>&1; then
  echo "Response is not a zip; saving as text for inspection." >&2
  mv "$OUT_ZIP" "$OUT_DIR/pwabuilder-response.txt"
  head -c 2000 "$OUT_DIR/pwabuilder-response.txt" >&2
  exit 1
fi

unzip -l "$OUT_ZIP"
echo "Saved: $OUT_ZIP"
echo "Next: install the .apk on your phone, and copy assetlinks.json to static/.well-known/ then redeploy."
