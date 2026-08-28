#!/usr/bin/env bash
# Assemble desktop/src-tauri/backend/ — the self-contained backend the .app
# ships as a Tauri resource (ADR local-first-distribution.md Phase 2):
#
#   backend/uv-python/cpython-3.11*/   standalone CPython (uv-managed,
#                                      python-build-standalone: relocatable)
#   backend/…/site-packages            asterism api+ingest[substrate]+step0+mcp
#   backend/oxigraph                   single-binary Oxigraph server
#   backend/demo-agent/app.py          Ask agent (spawned by asterism-local)
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
uv pip install --python "$PYBIN" --break-system-packages --no-sources \
  "$REPO/ingest[substrate]" "$REPO/step0" "$REPO/mcp" "$REPO/api"

# Relocation proof: importable-on-the-build-machine is NOT the bar (editable
# .pth files pass that and break after relocation). Require the real package
# directories and forbid editable install remnants.
SITE="$(dirname "$PYBIN")/../lib/python3.11/site-packages"
for pkg in asterism asterism_api asterism_step0 asterism_mcp morph_kgc; do
  [ -d "$SITE/$pkg" ] || { echo "bundle is not self-contained: $pkg missing from site-packages" >&2; exit 1; }
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

# --- repo payloads ---------------------------------------------------------
rm -rf "$DEST/demo-agent" "$DEST/datasets" "$DEST/ui-dist" "$DEST/manual"
mkdir -p "$DEST/demo-agent"
cp "$REPO/demo-agent/app.py" "$DEST/demo-agent/app.py"
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
