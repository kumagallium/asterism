# Asterism desktop shell

Native desktop app (Tauri v2) for the local-first distribution
([`docs/architecture/local-first-distribution.md`](../docs/architecture/local-first-distribution.md), Phase 2).

The shell owns one contract: spawn the `asterism-local` launcher (which itself
supervises Oxigraph and the demo-agent as children), wait for HTTP readiness on
a free loopback port, then open the native window at that URL. On quit it sends
SIGTERM so the launcher's own cleanup terminates the grandchildren.

## Run from a repo checkout (v1)

Prerequisites (once):

```bash
brew install oxigraph                     # or: cargo install oxigraph-cli
cd ui && npm ci && VITE_DEMO_MODE=live VITE_DEMO_AGENT_URL=/ npm run build && cd ..
cd api && uv venv .venv && uv pip install -e ../ingest && uv pip install -e '.[local]' && cd ..
```

Then:

```bash
cd desktop
npm install
npm run dev            # dev window
npm run build          # bundles desktop/src-tauri/target/release/bundle/macos/Asterism.app
```

The launcher is resolved in this order: `ASTERISM_LOCAL_CMD` env var →
`api/.venv/bin/asterism-local` found by walking up from the executable (covers
`tauri dev` and a .app built inside the repo) → `asterism-local` on PATH.
Backend output goes to the app log dir (macOS:
`~/Library/Logs/com.kumagallium.asterism/backend.log`).

## Self-contained bundle

`npm run build` runs `scripts/bundle-backend.sh` first (via
`beforeBuildCommand`), which assembles `src-tauri/backend/`:

- standalone CPython (uv-managed, relocatable) with the asterism packages
  installed non-editable (`ingest[substrate]` / `step0` / `mcp` / `api`),
- the Oxigraph single binary (pinned release, per-arch download),
- `demo-agent/app.py`, the bundled `datasets/`, and the built SPA.

The whole directory ships inside the .app as a Tauri resource (~370 MB), so
the built app runs on a machine with **no repo, no Python, no Docker, no
Homebrew** — the shell starts the backend as `python3 -m asterism_api.local`
with env pointing every payload at the bundle (console-script shebangs would
break on relocation). User data stays under the OS user-data dir as with
`asterism-local`. `tauri dev` skips the bundle and uses the repo checkout
resolution instead (fast iteration).

## Icon

`icon.svg` is the source (1024 canvas, centered 824px squircle + the favicon
mark). Regenerate the full set after editing it:

```bash
rsvg-convert -w 1024 -h 1024 icon.svg -o /tmp/icon-1024.png
npx tauri icon /tmp/icon-1024.png -o src-tauri/icons
```

(Do NOT render the raw `ui/public/favicon.svg` with qlmanage — it does not
upscale SVGs and drops the blur filters, which produced the blank-icon bug.)

## Not yet done

- **Signing / notarization / updater**: the release pipeline reuses Graphium's
  `tauri-build.yml` pattern (tauri-action) verbatim — same secret names:
  `TAURI_SIGNING_PRIVATE_KEY`, `APPLE_CERTIFICATE`,
  `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`, `APPLE_ID`,
  `APPLE_PASSWORD`, `APPLE_TEAM_ID`. Adding the workflow is a separate PR
  (workflow-only PRs get no CI runs scheduled in this repo — known trap).
  Nested Resources binaries (python/oxigraph) need signing coverage — Graphium
  ships a node runtime in resources the same way, so its config is the
  reference.
- **Windows**: grandchild cleanup needs a Job Object; the bundle script covers
  macOS/Linux asset names only.
- **Docling (PDF)**: not bundled — optional download later; PDF ingest
  degrades with the existing clear 4xx.
