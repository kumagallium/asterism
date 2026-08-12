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
uv pip install --python "$PYBIN" --break-system-packages \
  "$REPO/ingest[substrate]" "$REPO/step0" "$REPO/mcp" "$REPO/api"

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
rm -rf "$DEST/demo-agent" "$DEST/datasets" "$DEST/ui-dist"
mkdir -p "$DEST/demo-agent"
cp "$REPO/demo-agent/app.py" "$DEST/demo-agent/app.py"
cp -R "$REPO/datasets" "$DEST/datasets"

if [ ! -f "$REPO/ui/dist/index.html" ]; then
  echo "ui/dist not built. Run:" >&2
  echo "  cd ui && npm ci && VITE_DEMO_MODE=live VITE_DEMO_AGENT_URL=/ npm run build" >&2
  exit 1
fi
cp -R "$REPO/ui/dist" "$DEST/ui-dist"

echo "backend bundle ready: $DEST"
