#!/usr/bin/env bash
# Assemble desktop/src-tauri/backend/ — the self-contained backend the .app
# ships as a Tauri resource (ADR local-first-distribution.md Phase 2):
#
#   backend/uv-python/cpython-3.11*/   standalone CPython (uv-managed,
#                                      python-build-standalone: relocatable)
#   backend/…/site-packages            asterism api+ingest[substrate]+step0+mcp
#   backend/oxigraph                   single-binary Oxigraph server
#   backend/pandoc                     pandoc (unmodified official release; Word -> JATS)
#   backend/licenses/                  third-party license texts (pandoc: GPL-2.0-or-later)
#   backend/demo-agent/app.py          Ask agent (spawned by asterism-local)
#   backend/docling-sidecar/app.py     PDF sidecar (infra/docling-sidecar/app.py, unmodified).
#                                      Docling itself is NOT bundled: the user installs it on
#                                      demand (pdf_runtime/ in site-packages; ADR desktop-pdf-runtime.md)
#   backend/datasets/                  bundled example dataset content
#   backend/ui-dist/                   built SPA (live Ask mode)
#
# Idempotent; network only for the first python/oxigraph download and wheel
# resolution. macOS arm64/x86_64 (Linux later; Windows needs the .exe asset).
set -euo pipefail
cd "$(dirname "$0")/.."          # desktop/
REPO="$(cd .. && pwd)"
DEST="$PWD/src-tauri/backend"
OXI_VERSION="v0.5.9"
PANDOC_VERSION="3.12"

mkdir -p "$DEST"

# --- standalone Python + packages -----------------------------------------
export UV_PYTHON_INSTALL_DIR="$DEST/uv-python"
uv python install 3.11
PYBIN="$(ls -d "$DEST"/uv-python/cpython-3.11*/bin/python3 2>/dev/null | head -1)"
[ -x "$PYBIN" ] || { echo "standalone python not found under $DEST/uv-python" >&2; exit 1; }

# --break-system-packages: this standalone interpreter exists solely for the
# bundle; uv marks its own pythons externally-managed, which is exactly the
# guard we want to bypass here.
# --no-sources: api's tool.uv.sources marks the sibling packages EDITABLE;
# newer uv honors that even for `uv pip install <path>` and emits .pth links
# to the BUILD machine's checkout — which exist during the CI smoke test and
# vanish on the user's machine (the v0.1.0 .dmg shipped exactly that:
# ModuleNotFoundError: asterism). Snapshots must be real copies.
# --reinstall-package (the four local packages): a rebuild on a machine that
# already has $DEST must pick up source changes. Without it uv sees "same
# version, already installed" and keeps the OLD copy — a locally rebuilt bundle
# then runs stale code while its ui-dist is fresh (seen 2026-10-05: a re-bundle
# kept the previous pdf_runtime, and a check ran against it). CI builds from an
# empty $DEST, so this only ever bit local rebuilds.
uv pip install --python "$PYBIN" --break-system-packages --no-sources \
  --reinstall-package asterism-ingest --reinstall-package asterism-step0 \
  --reinstall-package asterism-mcp-tools --reinstall-package asterism-api \
  "$REPO/ingest[substrate]" "$REPO/step0" "$REPO/mcp" "$REPO/api"

# Relocation proof: importable-on-the-build-machine is NOT the bar (editable
# .pth files pass that and break after relocation). Require the real package
# directories and forbid editable install remnants.
SITE="$(dirname "$PYBIN")/../lib/python3.11/site-packages"
for pkg in asterism asterism_api asterism_step0 asterism_mcp morph_kgc; do
  [ -d "$SITE/$pkg" ] || { echo "bundle is not self-contained: $pkg missing from site-packages" >&2; exit 1; }
done
# The on-demand PDF runtime reads its pinned dependency list and model commits from the
# installed package. They are data files, not code: a wheel build that drops them would ship
# a "PDF runtime" that can never be installed.
for f in requirements-macos-arm64.txt models.json wheels/antlr4_python3_runtime-4.9.3-py3-none-any.whl; do
  [ -f "$SITE/asterism_api/pdf_runtime/$f" ] \
    || { echo "bundle is missing asterism_api/pdf_runtime/$f in site-packages" >&2; exit 1; }
done
if ls "$SITE"/_editable_impl_*.pth "$SITE"/__editable__* >/dev/null 2>&1; then
  echo "bundle contains editable-install remnants (.pth) — would break on relocation" >&2
  exit 1
fi

# --- console script shebangs: make them relocatable ------------------------
# pip/uv bake the BUILD machine's interpreter path into every console script's
# shebang (`#!/Users/runner/work/.../bin/python3` in CI). A shebang must be an
# absolute path — the kernel does not resolve relative ones — so a relocated
# bundle has every `asterism*` command dead on arrival:
#   bad interpreter: /Users/runner/work/... : no such file or directory
# The app itself never noticed: the Tauri shell starts the backend with
# `python -m`, bypassing the scripts. But anything *outside* the app that wants
# them is stuck — notably registering the MCP server with an AI client
# (`asterism --transport stdio`), which is the whole point of shipping it.
#
# Fix: replace the shebang with an sh/python polyglot that resolves the bundled
# interpreter relative to the script's own location. sh runs the exec line;
# python sees it as a triple-quoted string literal and ignores it.
BINDIR="$(dirname "$PYBIN")"
rewritten=0
for f in "$BINDIR"/*; do
  [ -f "$f" ] || continue
  head -1 "$f" 2>/dev/null | grep -qE '^#!.*/python[0-9.]*$' || continue
  tmp="$f.shebang.tmp"
  cat > "$tmp" <<'LAUNCHER'
#!/bin/sh
''''exec "$(dirname "$0")/python3" "$0" "$@" # '''
LAUNCHER
  tail -n +2 "$f" >> "$tmp"
  mv "$tmp" "$f"
  chmod +x "$f"
  rewritten=$((rewritten + 1))
done
echo "rewrote $rewritten console script shebang(s) to the relocatable launcher"
# Guard: no build-machine path may survive into the bundle.
if grep -rlE '^#!/.*(runner|/home/|/Users/)' "$BINDIR" 2>/dev/null | grep -q .; then
  echo "console scripts still carry a build-machine shebang" >&2
  grep -rlE '^#!/.*(runner|/home/|/Users/)' "$BINDIR" >&2
  exit 1
fi

# --- oxigraph single binary ------------------------------------------------
if [ ! -x "$DEST/oxigraph" ]; then
  case "$(uname -sm)" in
    "Darwin arm64")  ASSET="oxigraph_${OXI_VERSION}_aarch64_apple" ;;
    "Darwin x86_64") ASSET="oxigraph_${OXI_VERSION}_x86_64_apple" ;;
    "Linux aarch64") ASSET="oxigraph_${OXI_VERSION}_aarch64_linux_gnu" ;;
    "Linux x86_64")  ASSET="oxigraph_${OXI_VERSION}_x86_64_linux_gnu" ;;
    *) echo "unsupported platform: $(uname -sm)" >&2; exit 1 ;;
  esac
  URL="https://github.com/oxigraph/oxigraph/releases/download/${OXI_VERSION}/${ASSET}"
  echo "downloading ${URL}"
  curl -fL --retry 3 -o "$DEST/oxigraph.tmp" "$URL"
  chmod +x "$DEST/oxigraph.tmp"
  mv "$DEST/oxigraph.tmp" "$DEST/oxigraph"
fi
"$DEST/oxigraph" --version >/dev/null

# --- pandoc (Word -> JATS; GPL-2.0-or-later, run as a child process only) ---
# Pinned official release, sha256-verified. Only bin/pandoc is taken from the
# archive. Re-fetched when the bundled binary is missing or its version differs.
case "$(uname -sm)" in
  "Darwin arm64")  PANDOC_ASSET="pandoc-${PANDOC_VERSION}-arm64-macOS.zip"
                   PANDOC_SHA256="f148ca09c9f36594db527a9fc988ad736290ce428f79594c50208cd1ec58b3c0" ;;
  "Darwin x86_64") PANDOC_ASSET="pandoc-${PANDOC_VERSION}-x86_64-macOS.zip"
                   PANDOC_SHA256="18577f9460c3dc5d2651ad3bab37d513bc2034a5a777fbe18fa0a5acf2e936ea" ;;
  "Linux x86_64")  PANDOC_ASSET="pandoc-${PANDOC_VERSION}-linux-amd64.tar.gz"
                   PANDOC_SHA256="67d7d011fed8c8543306022b985b9b2499ab9b74818df91d8727c7e9ebc5ba06" ;;
  "Linux aarch64") PANDOC_ASSET="pandoc-${PANDOC_VERSION}-linux-arm64.tar.gz"
                   PANDOC_SHA256="6cefcf7100e23a99447c26f89d1ff5b253f3407fcef99a9e27ae06f3ed16cb82" ;;
  *) echo "unsupported platform: $(uname -sm)" >&2; exit 1 ;;
esac
if [ ! -x "$DEST/pandoc" ] || [ "$("$DEST/pandoc" --version 2>/dev/null | head -1)" != "pandoc ${PANDOC_VERSION}" ]; then
  PANDOC_URL="https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/${PANDOC_ASSET}"
  PANDOC_TMP="$(mktemp -d)"
  echo "downloading ${PANDOC_URL}"
  curl -fL --retry 3 -o "$PANDOC_TMP/$PANDOC_ASSET" "$PANDOC_URL"
  ACTUAL_SHA256="$(shasum -a 256 "$PANDOC_TMP/$PANDOC_ASSET" | awk '{print $1}')"
  if [ "$ACTUAL_SHA256" != "$PANDOC_SHA256" ]; then
    echo "pandoc sha256 mismatch for ${PANDOC_ASSET}: expected ${PANDOC_SHA256}, got ${ACTUAL_SHA256}" >&2
    rm -rf "$PANDOC_TMP"
    exit 1
  fi
  mkdir "$PANDOC_TMP/x"
  case "$PANDOC_ASSET" in
    *.zip)    unzip -q "$PANDOC_TMP/$PANDOC_ASSET" '*/bin/pandoc' -d "$PANDOC_TMP/x" ;;
    *.tar.gz) tar -xzf "$PANDOC_TMP/$PANDOC_ASSET" -C "$PANDOC_TMP/x" --wildcards '*/bin/pandoc' 2>/dev/null \
              || tar -xzf "$PANDOC_TMP/$PANDOC_ASSET" -C "$PANDOC_TMP/x" "pandoc-${PANDOC_VERSION}/bin/pandoc" ;;
  esac
  PANDOC_SRC="$(ls "$PANDOC_TMP"/x/*/bin/pandoc 2>/dev/null | head -1)"
  [ -f "$PANDOC_SRC" ] || { echo "bin/pandoc not found in ${PANDOC_ASSET}" >&2; rm -rf "$PANDOC_TMP"; exit 1; }
  cp "$PANDOC_SRC" "$DEST/pandoc.tmp"
  chmod +x "$DEST/pandoc.tmp"
  mv "$DEST/pandoc.tmp" "$DEST/pandoc"
  rm -rf "$PANDOC_TMP"
fi
[ "$("$DEST/pandoc" --version | head -1)" = "pandoc ${PANDOC_VERSION}" ] \
  || { echo "bundled pandoc is not version ${PANDOC_VERSION}" >&2; exit 1; }

# Third-party license texts ship next to the binary (GPL notice + source pointer).
rm -rf "$DEST/licenses"
mkdir -p "$DEST/licenses/pandoc"
cp third-party/pandoc/COPYING.md third-party/pandoc/COPYRIGHT third-party/pandoc/README.md "$DEST/licenses/pandoc/"

# --- repo payloads ---------------------------------------------------------
rm -rf "$DEST/demo-agent" "$DEST/docling-sidecar" "$DEST/datasets" "$DEST/ui-dist" "$DEST/manual"
mkdir -p "$DEST/demo-agent" "$DEST/docling-sidecar"
cp "$REPO/demo-agent/app.py" "$DEST/demo-agent/app.py"
cp "$REPO/infra/docling-sidecar/app.py" "$DEST/docling-sidecar/app.py"
cp -R "$REPO/datasets" "$DEST/datasets"
# The user manual is ALSO the consult chat's knowledge source: the api's
# upward search reaches $DEST/manual/ja from site-packages. Without it the
# shipped .app ran consult without the manual (dev checkouts masked this).
cp -R "$REPO/manual" "$DEST/manual"
[ -d "$DEST/manual/ja" ] || { echo "manual/ja missing from bundle" >&2; exit 1; }

if [ ! -f "$REPO/ui/dist/index.html" ]; then
  echo "ui/dist not built. Run:" >&2
  echo "  cd ui && npm ci && VITE_DEMO_MODE=live VITE_DEMO_AGENT_URL=/ npm run build" >&2
  exit 1
fi
cp -R "$REPO/ui/dist" "$DEST/ui-dist"

echo "backend bundle ready: $DEST"
