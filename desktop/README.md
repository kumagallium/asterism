# Asterism desktop shell

Native desktop app (Tauri v2) for the local-first distribution
([`docs/architecture/local-first-distribution.md`](../docs/architecture/local-first-distribution.md), Phase 2).

The shell owns one contract: spawn the `asterism-local` launcher (which itself
supervises Oxigraph and the demo-agent as children), wait for HTTP readiness on
the fixed loopback port 8765, then open the native window at that URL. On quit it
sends SIGTERM so the launcher's own cleanup terminates the grandchildren.

## 起動中の画面と、うまくいかなかったときの出口

アイコンをダブルクリックした瞬間から `desktop/splash/index.html` の小窓が出る
（窓は `tauri.conf.json` の `app.windows` に定義されているので、バックエンドの
準備を待たない）。この窓は 3 つの役をひとりで担う:

- **準備中**: 「Asterism を準備しています…」＋「初めての起動は 1 分ほどかかること
  があります。データはあなたのパソコンの中だけにあります。」＋不確定プログレス。
  20 秒を超えると「まだ動いています。」に変わり、経過秒を出す。
- **停止カード**（ADR `kantan-mode-two-tier-ux.md` K11）: 起動の失敗は
  ネイティブダイアログ（OK 一つ＝即終了）ではなく、この窓に平易な 1 行＋
  ［もう一度試す］［ログを開く］［内容をコピー］［やめる］＋
  `詳しい内容（技術情報）` の折りたたみとして描く。ユーザーが選ぶまで終了しない。
  ［もう一度試す］は残っている子プロセスを terminate してからポートを取り直す。
- **更新のダウンロード中／終了処理中**: 進捗と「データを安全に閉じています」。

シェル側（Rust）は**どのメッセージを出すか**だけを決め、ja/en の文面は splash の
`TEXT` テーブルに 1 か所でまとまっている（`lang` はシェルが OS 設定から判定して
渡すので、ネイティブメニューと言語がずれない）。窓すら作れなかったときだけ、
最後の手段としてネイティブダイアログが出る。

splash はローカルページなので、シェル自身の `boot_status` / `boot_action` コマンド
だけで会話する（プラグイン権限は不要・`main` からの呼び出しは Tauri の ACL と
ラベル検査の二重で拒否される）。

起動時の枝分かれ 2 つ:

- 8765 が塞がっていて、その相手が **Asterism 自身**（`/health` が `"oxigraph"` を
  返す）なら、2 つ目のバックエンドを起動せずその URL を開くだけにする。
- 相手が別のプログラムなら、別ポートで動かす前に「設定が保存されません」と伝えて
  ［このまま使う］［やめる］を出し、続ける場合は窓の URL に `?port_fallback=1` を
  付ける（SPA 側でその旨の帯を出すためのしるし。UI 側は未実装）。
- `grant_spa_update_ipc` に失敗した場合はバックエンドに `ASTERISM_UPDATER_IPC=0`
  を渡す（設定→このアプリ でメニューバー経由の更新を案内するためのしるし。
  api / UI 側は未実装）。

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
→ download → relaunch) for when the page cannot help. Its dialog uses the same
wording as the banner (**再起動して更新** / **後で**), says that data and settings
are kept, and asks the user to let a running ingest finish first. While the
download runs the menu item is greyed out (no second download) and the splash
window comes back with the percentage; a failure lands in a stop card with
［もう一度試す］［ダウンロードページを開く］ instead of a raw error string.

Every platform now also gets a **ヘルプ** menu — **ログを開く（不具合の相談用）**
(reveals `backend.log` in Finder/Explorer), **はじめかた**, **アップデートを確認…**
— because when the page is blank the menu bar is the only surface left.

Menu labels and native dialogs follow the OS language (ja/en; `ASTERISM_LANG`
overrides), so an English install no longer quotes Japanese menu names.

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

> **未署名ビルドを配る間は、この 1 文をダウンロード導線に必ず添える。**
> 「初回だけ、アプリを右クリック →「開く」を選んでください（Apple の確認画面が
> 出ます）。」/ "The first time only, right-click the app and choose Open (macOS
> will ask you to confirm)."
> Apple の署名 secrets が無いまま配られた `.dmg` は、初回起動で「開発元を検証
> できないため開けません」（新しめの macOS では「壊れているため開けません」）に
> なり、選べるのは「ゴミ箱に入れる」だけ＝スプラッシュにも停止カードにも到達
> できない。**本筋の解は署名/公証 secrets の登録（下記）**で、この一文はそれまで
> の暫定。リリース本文への自動挿入は `.github/workflows/desktop-release.yml`
> 側の作業（この PR では workflow を触らない）。

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
