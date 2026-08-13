#!/usr/bin/env bash
# Deep-sign, notarize, and staple a Tauri .app that bundles loose Mach-O
# binaries in Resources/ (the standalone CPython runtime + the oxigraph
# binary + every C-extension .so/.dylib).
#
# Tauri signs only the OUTER app, so notarization rejects the unsigned nested
# binaries — "The binary is not signed with a valid Developer ID certificate",
# "does not include a secure timestamp", "hardened runtime not enabled"
# (observed on v0.13.1's Resources/backend/oxigraph + .../python3). This signs
# every Mach-O inside-out with hardened runtime + secure timestamp, signs the
# app with the Python entitlements (disable-library-validation so python3 can
# load its .so at runtime), then notarizes with notarytool and staples the
# ticket into the app.
#
# Requires (env): APPLE_CERTIFICATE (base64 .p12), APPLE_CERTIFICATE_PASSWORD,
# APPLE_SIGNING_IDENTITY, APPLE_ID, APPLE_PASSWORD (app-specific), APPLE_TEAM_ID.
# Usage: sign-notarize-macos.sh <path-to-.app>
set -euo pipefail

APP="$1"
HERE="$(cd "$(dirname "$0")" && pwd)"
ENTITLEMENTS="$HERE/../src-tauri/entitlements.plist"
TMP="${RUNNER_TEMP:-/tmp}"

: "${APPLE_CERTIFICATE:?APPLE_CERTIFICATE is required}"
: "${APPLE_CERTIFICATE_PASSWORD:?}"
: "${APPLE_SIGNING_IDENTITY:?}"
: "${APPLE_ID:?}"
: "${APPLE_PASSWORD:?}"
: "${APPLE_TEAM_ID:?}"

# --- import the signing cert into a dedicated keychain ---------------------
KEYCHAIN="asterism-signing.keychain"
KC_PW="$(uuidgen)"
security create-keychain -p "$KC_PW" "$KEYCHAIN"
security set-keychain-settings -lut 21600 "$KEYCHAIN"
security default-keychain -s "$KEYCHAIN"
security unlock-keychain -p "$KC_PW" "$KEYCHAIN"
echo "$APPLE_CERTIFICATE" | base64 --decode > "$TMP/cert.p12"
security import "$TMP/cert.p12" -k "$KEYCHAIN" -P "$APPLE_CERTIFICATE_PASSWORD" \
  -T /usr/bin/codesign -T /usr/bin/security
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$KC_PW" "$KEYCHAIN" >/dev/null
rm -f "$TMP/cert.p12"

sign() {
  codesign --force --timestamp --options runtime --keychain "$KEYCHAIN" \
    --entitlements "$ENTITLEMENTS" --sign "$APPLE_SIGNING_IDENTITY" "$1"
}

# --- sign every nested Mach-O, then the app bundle (inside-out) -------------
echo "Signing nested Mach-O binaries…"
COUNT=0
while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q "Mach-O"; then
    sign "$f"
    COUNT=$((COUNT + 1))
  fi
done < <(find "$APP/Contents/Resources" -type f \( -name "*.so" -o -name "*.dylib" -o -perm +111 \) -print0)
echo "  signed $COUNT nested binaries"

echo "Signing the app bundle…"
sign "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

# --- notarize + staple -----------------------------------------------------
echo "Submitting for notarization (this can take several minutes)…"
ZIP="$TMP/Asterism-notarize.zip"
ditto -c -k --keepParent "$APP" "$ZIP"
set +e
SUBMIT_OUT=$(xcrun notarytool submit "$ZIP" \
  --apple-id "$APPLE_ID" --password "$APPLE_PASSWORD" --team-id "$APPLE_TEAM_ID" \
  --wait 2>&1)
SUBMIT_RC=$?
set -e
echo "$SUBMIT_OUT"
SUBMISSION_ID=$(echo "$SUBMIT_OUT" | awk '/id:/{print $2; exit}')
if [ $SUBMIT_RC -ne 0 ] || ! echo "$SUBMIT_OUT" | grep -q "status: Accepted"; then
  echo "::error::notarization did not succeed — fetching the detailed log"
  [ -n "$SUBMISSION_ID" ] && xcrun notarytool log "$SUBMISSION_ID" \
    --apple-id "$APPLE_ID" --password "$APPLE_PASSWORD" --team-id "$APPLE_TEAM_ID" || true
  exit 1
fi

xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
echo "signed + notarized + stapled: $APP"
