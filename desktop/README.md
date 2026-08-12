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

## Not yet in v1

- **Self-contained bundle**: Python runtime / oxigraph binary / built SPA are
  not bundled yet — v1 needs the repo checkout above. The follow-up mirrors
  Graphium's sidecar layout (`bundle.externalBin` + `resources`).
- **Signing / notarization / updater**: the release pipeline reuses Graphium's
  `tauri-build.yml` pattern (tauri-action) verbatim — same secret names:
  `TAURI_SIGNING_PRIVATE_KEY`, `APPLE_CERTIFICATE`,
  `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`, `APPLE_ID`,
  `APPLE_PASSWORD`, `APPLE_TEAM_ID`. Adding the workflow is a separate PR
  (workflow-only PRs get no CI runs scheduled in this repo — known trap).
