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

## Auto-update

The everyday flow lives in the SPA, the same way Graphium does it
(`ui/src/desktop/updater.ts` + `UpdateBanner.tsx`): 5 s after the window opens
and every 24 h it calls `@tauri-apps/plugin-updater`'s `check()`; when a newer
signed version exists, a banner across the top of the window offers
**再起動して更新** (download with progress → install → relaunch) and
**今すぐ確認** (re-check). Settings → このアプリ → 今すぐ確認 runs the same check.

The window is a remote `http://127.0.0.1:<port>` page, and Tauri grants remote
origins no IPC unless a capability names them — so the shell registers one at
runtime for exactly that origin (`grant_spa_update_ipc` in `src/lib.rs`, port
included so the random-port fallback still works). It opens only
`updater:default`, `process:allow-restart` and `core:resources:allow-close`;
endpoints and the minisign pubkey are fixed in `tauri.conf.json`, so the page
can install nothing but a signed release.

The app menu item **アップデートを確認…** remains as the native fallback (dialog
→ download → relaunch) for when the page cannot help.

Do NOT press "再起動して更新" in a `tauri dev` binary: the updater's macOS
install target is derived from the executable path, and for an unbundled
binary that is `target/debug/` itself. Verify with a built `.app`
(`tauri build --debug --bundles app`, launched directly with `ASTERISM_LOCAL_CMD`
/ `ASTERISM_UI_DIST` / `ASTERISM_LOCAL_HOME` set), which is what §6.2 of the ADR
did end to end.

Version is kept in sync by tagpr: `.tagpr`'s `versionFile` bumps both `VERSION`
and `desktop/src-tauri/tauri.conf.json`, so the bundled app version tracks the
release and the "current < latest" comparison is correct.

### Release pipeline (`.github/workflows/desktop-release.yml`)

On a published GitHub release, the workflow builds the macOS app, always
uploads the `.dmg` for direct download, and — when the signing secret is
present — also produces the signed updater artifacts (`Asterism_aarch64.app.tar.gz`
+ `.sig`) and a `latest.json`, uploading all three to the release. The feed the
app polls is the GitHub Pages copy (`docs/updater/latest.json`, written by the
workflow's last step only after a successful build — ADR §6.1); the release
asset URL is kept as the second endpoint for already-shipped builds.

### One-time signing setup (required for auto-update to work)

The updater verifies a minisign signature. A keypair was generated at
`~/.asterism/updater.key` (private) + `~/.asterism/updater.key.pub` (public,
already embedded as `plugins.updater.pubkey` in `tauri.conf.json`). Add the
**private** key as a repo secret so the release workflow can sign:

```bash
gh secret set TAURI_SIGNING_PRIVATE_KEY < ~/.asterism/updater.key
```

(The key was generated with an empty password, so no
`TAURI_SIGNING_PRIVATE_KEY_PASSWORD` secret is needed. Keep the private key
file safe / back it up — losing it means shipping a new pubkey, which breaks
updates for already-installed apps.) Regenerate with
`npx tauri signer generate -w <path> --password ""`.

Optional but recommended: add the Apple Developer signing secrets
(`APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`,
`APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` — same names as Graphium) so the
installed/updated app is notarized and not Gatekeeper-blocked.

## Storage location

By default the backend keeps all data (datasets, graphs, chat threads,
settings) under the OS user-data dir, e.g. macOS
`~/Library/Application Support/Asterism` — unchanged from `asterism-local`'s
own default. Settings → ストレージ lets the user pick a different folder; the
shell saves that choice in its own small config file (`settings.json` under
the Tauri app config dir, *not* inside the data dir itself, since the data
dir is exactly what's being redirected) and passes it to the backend as
`--data-dir` on the next launch (`get_data_home_override` /
`set_data_home_override` IPC, `src/settings.rs`). The change takes effect on
restart — same as Graphium — and existing data at the old location is **not**
moved automatically.

## Not yet done

- **Windows**: grandchild cleanup needs a Job Object; the bundle script and the
  updater's `latest.json` cover macOS aarch64 only.
- **Docling (PDF)**: not bundled — optional download later; PDF ingest
  degrades with the existing clear 4xx.
