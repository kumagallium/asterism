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
# With a second arg (VERSION) it also builds a Developer-ID-signed, notarized,
# stapled .dmg (Asterism_<VERSION>_aarch64.dmg in the current dir) — so the
# downloaded disk image itself passes Gatekeeper, not just the app inside it.
#
# Requires (env): APPLE_CERTIFICATE (base64 .p12), APPLE_CERTIFICATE_PASSWORD,
# APPLE_SIGNING_IDENTITY, APPLE_ID, APPLE_PASSWORD (app-specific), APPLE_TEAM_ID.
# Usage: sign-notarize-macos.sh <path-to-.app> [VERSION]
set -euo pipefail

APP="$1"
VERSION="${2:-}"
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

# submit a file (zip / dmg) to notarization, wait, and surface the log on
# failure (notarytool exits non-zero and prints only a summary otherwise).
submit_notarize() {
  local out rc id
  set +e
  out=$(xcrun notarytool submit "$1" \
    --apple-id "$APPLE_ID" --password "$APPLE_PASSWORD" --team-id "$APPLE_TEAM_ID" \
    --wait 2>&1)
  rc=$?
  set -e
  echo "$out"
  id=$(echo "$out" | awk '/id:/{print $2; exit}')
  if [ $rc -ne 0 ] || ! echo "$out" | grep -q "status: Accepted"; then
    echo "::error::notarization failed for $1 — detailed log follows"
    [ -n "$id" ] && xcrun notarytool log "$id" \
      --apple-id "$APPLE_ID" --password "$APPLE_PASSWORD" --team-id "$APPLE_TEAM_ID" || true
    return 1
  fi
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

# --- notarize + staple the app ---------------------------------------------
echo "Submitting the app for notarization (this can take several minutes)…"
ZIP="$TMP/Asterism-notarize.zip"
ditto -c -k --keepParent "$APP" "$ZIP"
submit_notarize "$ZIP"
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
echo "signed + notarized + stapled: $APP"

# hdiutil is flaky on CI: it fails with "Resource busy" when a stale /Volumes
# mount or a background indexer still holds the volume name or the staging dir.
# Observed on the v0.17.1 release (2026-08-18) — everything up to and including
# notarization succeeded and the run died on the last step, leaving the release
# with no .dmg and no updater feed. Detach any stale mount, then retry with
# backoff; only a persistent failure is a real one.
make_dmg() {
  local dmg="$1" stage="$2" attempt
  for attempt in 1 2 3 4 5; do
    # A previous (failed) create can leave /Volumes/Asterism attached; a mount
    # left over from ANOTHER job on the same runner counts too.
    if [ -d "/Volumes/Asterism" ]; then
      echo "detaching stale /Volumes/Asterism"
      hdiutil detach "/Volumes/Asterism" -force >/dev/null 2>&1 || true
    fi
    if hdiutil create -volname "Asterism" -srcfolder "$stage" -ov -format UDZO "$dmg"; then
      return 0
    fi
    echo "hdiutil create failed (attempt $attempt) — retrying in $((attempt * 10))s"
    rm -f "$dmg"
    sleep $((attempt * 10))
  done
  echo "hdiutil create failed after 5 attempts" >&2
  return 1
}

# --- build + sign + notarize + staple the .dmg -----------------------------
if [ -n "$VERSION" ]; then
  echo "Building the .dmg from the stapled app…"
  DMG="Asterism_${VERSION}_aarch64.dmg"
  STAGE="$TMP/dmg-stage"
  rm -rf "$STAGE"; mkdir -p "$STAGE"
  cp -R "$APP" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  rm -f "$DMG"
  make_dmg "$DMG" "$STAGE"
  codesign --force --timestamp --keychain "$KEYCHAIN" \
    --sign "$APPLE_SIGNING_IDENTITY" "$DMG"
  echo "Submitting the .dmg for notarization…"
  submit_notarize "$DMG"
  xcrun stapler staple "$DMG"
  xcrun stapler validate "$DMG"
  echo "signed + notarized + stapled: $DMG"
fi
