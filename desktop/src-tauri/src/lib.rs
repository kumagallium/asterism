//! Asterism desktop shell — Phase 2 v1 of ADR `local-first-distribution.md`.
//!
//! The shell owns exactly one contract: spawn the `asterism-local` launcher
//! (which itself supervises Oxigraph and the demo-agent as children) as a
//! process-group leader, wait for HTTP readiness on a fixed loopback port, then
//! open the native window at that URL. On quit, signal the whole group
//! (SIGTERM, then SIGKILL) so no grandchild can be orphaned.
//!
//! v1 resolves the launcher from a repo checkout (`api/.venv/bin/asterism-local`
//! found by walking up from the executable, or `ASTERISM_LOCAL_CMD`, or PATH).
//! Bundling a self-contained Python runtime is the follow-up step; the process
//! contract stays the same, mirroring Graphium's sidecar layout.
//!
//! What the user sees (ADR `kantan-mode-two-tier-ux.md` K4/K11): a splash
//! window is declared in `tauri.conf.json`, so it is on screen from the moment
//! the icon is double-clicked — never a blank minute. Every start-up failure
//! draws a STOP CARD into that same window instead of a one-button native
//! dialog: one plain sentence, at least two ways out ("もう一度試す" /
//! "ログを開く" / "やめる"), and the technical text folded away behind
//! "詳しい内容（技術情報）". The shell only decides WHICH message; the wording
//! for both languages lives in `desktop/splash/index.html`. A native dialog is
//! the last resort, for when even a window cannot be drawn.
//!
//! Updates (ADR local-first-distribution.md §6.2): the SPA owns the everyday
//! flow — it checks on launch and every 24 h and shows a banner at the top of
//! the window with a one-click "download, install, relaunch" (the same
//! `@tauri-apps/plugin-updater` calls Graphium makes). The shell's part is to
//! grant that loopback origin exactly the updater/relaunch IPC it needs
//! (`grant_spa_update_ipc`) and to keep the native menu item as the fallback
//! that works even when the page does not.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::ipc::CapabilityBuilder;
use tauri::menu::{MenuBuilder, MenuItem, MenuItemBuilder, MenuItemKind, SubmenuBuilder};
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

/// How long to wait for the backend before ASKING the user what to do. Not a
/// deadline any more: as long as the child is alive the user can keep waiting
/// (a first launch unpacks ~370 MB and gets a Gatekeeper scan).
const READY_TIMEOUT: Duration = Duration::from_secs(60);

const SPLASH: &str = "splash";
const MAIN: &str = "main";

/// Where "ダウンロードページを開く" goes.
const RELEASES_URL: &str = "https://github.com/kumagallium/asterism/releases/latest";
/// Where the Help menu's "はじめかた" goes.
const GUIDE_URL: &str = "https://github.com/kumagallium/asterism#readme";

/// Shown (as the technical block) when no launcher is found in a repo checkout.
/// Developer-facing on purpose — this path cannot be reached from a built .app.
const DEV_SETUP: &str = "asterism-local not found (repo checkout).\n\n\
     cd api && uv venv .venv \\\n       && uv pip install -e ../ingest && uv pip install -e '.[local]'\n\n\
     ASTERISM_LOCAL_CMD=<path> overrides the lookup.";

// ---------------------------------------------------------------------------
// Shared state
// ---------------------------------------------------------------------------

/// What the splash window is showing right now. The shell names the message
/// and the buttons; `desktop/splash/index.html` owns the ja/en wording, so no
/// user-facing sentence is duplicated across languages here.
#[derive(Clone, Serialize)]
struct Status {
    /// Language the shell detected, so the window agrees with the native menu.
    lang: &'static str,
    /// "starting" | "stopping" | "quitting" | "updating" | "card".
    phase: &'static str,
    /// Download progress, when the feed gives a content length.
    percent: Option<u8>,
    /// Message key for a stop card (empty while merely working).
    key: &'static str,
    /// One extra plain sentence, read deterministically off the log tail.
    hint: &'static str,
    /// The technical text. Kept in full — just folded away.
    detail: String,
    /// Open the technical block: in a repo checkout the command IS the answer.
    detail_open: bool,
    /// Button ids, in display order.
    actions: Vec<&'static str>,
}

impl Status {
    fn working(phase: &'static str) -> Self {
        Self {
            lang: ui_lang(),
            phase,
            percent: None,
            key: "",
            hint: "",
            detail: String::new(),
            detail_open: false,
            actions: Vec::new(),
        }
    }

    fn card(key: &'static str, detail: String, actions: &[&'static str]) -> Self {
        Self {
            key,
            detail,
            actions: actions.to_vec(),
            ..Self::working("card")
        }
    }

    fn with_hint(mut self, hint: &'static str) -> Self {
        self.hint = hint;
        self
    }

    fn open_detail(mut self) -> Self {
        self.detail_open = true;
        self
    }
}

impl Default for Status {
    fn default() -> Self {
        Self::working("starting")
    }
}

#[derive(Default)]
struct Shell {
    backend: Mutex<Option<Child>>,
    status: Mutex<Status>,
    /// Set by the splash window, read by whichever thread is blocked in `halt`.
    decision: Mutex<Option<String>>,
    log_path: Mutex<Option<PathBuf>>,
    updating: AtomicBool,
    shutting_down: AtomicBool,
    terminated: AtomicBool,
}

/// Where a stop card can send the boot sequence next.
enum Flow {
    Done,
    Retry,
    Quit,
}

// ---------------------------------------------------------------------------
// Language (native dialogs + menu; the splash mirrors this choice)
// ---------------------------------------------------------------------------

/// "ja" or "en", decided once per launch. A macOS .app started from Finder
/// inherits no LANG, so fall back to the system preference; ASTERISM_LANG
/// overrides both.
fn ui_lang() -> &'static str {
    static LANG: OnceLock<&'static str> = OnceLock::new();
    *LANG.get_or_init(|| {
        if detect_lang().starts_with("ja") {
            "ja"
        } else {
            "en"
        }
    })
}

fn detect_lang() -> String {
    for key in ["ASTERISM_LANG", "LC_ALL", "LC_MESSAGES", "LANG"] {
        if let Ok(value) = std::env::var(key) {
            if !value.is_empty() {
                return value.to_lowercase();
            }
        }
    }
    #[cfg(target_os = "macos")]
    if let Ok(out) = Command::new("defaults")
        .args(["read", "-g", "AppleLocale"])
        .output()
    {
        if out.status.success() {
            return String::from_utf8_lossy(&out.stdout).trim().to_lowercase();
        }
    }
    String::new()
}

/// Native-surface wording (menu items and native dialogs). The splash window
/// keeps its own copy for everything it draws — see `desktop/splash/index.html`.
fn t(key: &str) -> &'static str {
    let ja = ui_lang() == "ja";
    match key {
        "menu.checkUpdate" => {
            if ja {
                "アップデートを確認…"
            } else {
                "Check for Updates…"
            }
        }
        "menu.openLog" => {
            if ja {
                "ログを開く（不具合の相談用）"
            } else {
                "Open the Log (for reporting problems)"
            }
        }
        "menu.guide" => {
            if ja {
                "はじめかた"
            } else {
                "Getting Started"
            }
        }
        "menu.edit" => {
            if ja {
                "編集"
            } else {
                "Edit"
            }
        }
        "menu.window" => {
            if ja {
                "ウインドウ"
            } else {
                "Window"
            }
        }
        "menu.help" => {
            if ja {
                "ヘルプ"
            } else {
                "Help"
            }
        }
        "update.title" => {
            if ja {
                "Asterism のアップデート"
            } else {
                "Asterism update"
            }
        }
        "update.notes" => {
            if ja {
                "更新すると Asterism を再起動します。取り込んだデータや設定はそのまま残ります。\nいま取り込みや AI の処理が動いているときは、終わってから更新してください。"
            } else {
                "Asterism restarts to update. Your data and settings are kept.\nIf a reading or ingest job is running right now, please let it finish first."
            }
        }
        "update.install" => {
            if ja {
                "再起動して更新"
            } else {
                "Restart and update"
            }
        }
        "update.later" => {
            if ja {
                "後で"
            } else {
                "Later"
            }
        }
        "update.upToDate" => {
            if ja {
                "お使いの Asterism は最新です。"
            } else {
                "Asterism is up to date."
            }
        }
        // Checking failed for a reason the user may be able to do something
        // about (most often: no internet).
        "update.checkFailed" => {
            if ja {
                "更新を確認できませんでした（インターネットにつながっていない可能性があります）。"
            } else {
                "Couldn't check for updates (you may be offline)."
            }
        }
        // The updater itself could not start — waiting does not help, so the
        // wording does not invite a retry.
        "update.unavailable" => {
            if ja {
                "更新を確認できませんでした。"
            } else {
                "Couldn't check for updates."
            }
        }
        "update.failed" => {
            if ja {
                "更新できませんでした。いまお使いの Asterism はそのまま使えます。"
            } else {
                "The update didn't go through. The Asterism you have now still works."
            }
        }
        // Last resort only: shown when not even a window could be drawn.
        "fallback.failed" => {
            if ja {
                "Asterism を起動できませんでした。"
            } else {
                "Asterism couldn't start."
            }
        }
        _ => "",
    }
}

fn update_available_line(new: &str, current: &str) -> String {
    if ui_lang() == "ja" {
        format!("Asterism {new} が利用できます（いまお使いのバージョンは {current}）。")
    } else {
        format!("Asterism {new} is available (you have {current}).")
    }
}

// ---------------------------------------------------------------------------
// Ports and readiness
// ---------------------------------------------------------------------------

fn free_port() -> std::io::Result<u16> {
    Ok(TcpListener::bind("127.0.0.1:0")?.local_addr()?.port())
}

// The window loads http://127.0.0.1:<port>/. The browser keys localStorage
// (registered models, default model, remembered API keys — ui/src/settings)
// by ORIGIN, so a per-launch random port would silently wipe every setting on
// each restart. Pin a fixed loopback port so the origin — and therefore the
// stored settings — is stable across launches (the same reason Graphium pins
// 127.0.0.1:3001).
const PREFERRED_PORT: u16 = 8765;

/// What to do about the fixed port.
enum PortUse {
    /// It was free: start a backend there.
    Fresh(u16),
    /// An Asterism is already answering there: use it instead of starting a
    /// second one (two backends on one data dir is the worse outcome).
    Attach,
    /// Something else holds it. We can still run, but on another origin — and
    /// that costs the user their stored settings for this session, so we say so.
    Fallback(u16),
}

fn resolve_port() -> PortUse {
    match TcpListener::bind(("127.0.0.1", PREFERRED_PORT)) {
        Ok(listener) => {
            drop(listener);
            PortUse::Fresh(PREFERRED_PORT)
        }
        Err(_) if asterism_at(PREFERRED_PORT) => PortUse::Attach,
        Err(_) => PortUse::Fallback(free_port().unwrap_or(PREFERRED_PORT)),
    }
}

/// Minimal HTTP GET on loopback; returns the raw response, or None if nothing
/// answered in time.
fn http_get(port: u16, path: &str) -> Option<String> {
    let addr = format!("127.0.0.1:{port}").parse().ok()?;
    let mut stream = TcpStream::connect_timeout(&addr, Duration::from_millis(500)).ok()?;
    let _ = stream.set_read_timeout(Some(Duration::from_millis(700)));
    let request =
        format!("GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n");
    stream.write_all(request.as_bytes()).ok()?;
    let mut buf = Vec::new();
    // A read timeout ends the read with an error, but whatever arrived first is
    // already in `buf` — enough to see the status line.
    let _ = stream.read_to_end(&mut buf);
    Some(String::from_utf8_lossy(&buf).into_owned())
}

/// Any HTTP status line counts — `/health` may legitimately be 503 while the
/// store warms up, but the SPA is served as soon as uvicorn answers.
fn http_ready(port: u16) -> bool {
    http_get(port, "/health")
        .map(|response| response.starts_with("HTTP/1."))
        .unwrap_or(false)
}

/// Is the program already holding the port an Asterism? `/health` answers
/// `{"status": …, "oxigraph": …}` in both the 200 and the 503 case.
fn asterism_at(port: u16) -> bool {
    http_get(port, "/health")
        .map(|response| response.contains("\"oxigraph\""))
        .unwrap_or(false)
}

// ---------------------------------------------------------------------------
// Backend resolution
// ---------------------------------------------------------------------------

/// How to start the backend: program + leading args + env the layout needs.
struct BackendCmd {
    program: PathBuf,
    prefix_args: Vec<String>,
    envs: Vec<(String, PathBuf)>,
}

/// Self-contained layout: the .app ships `Resources/backend/` (standalone
/// CPython with the asterism packages installed, the oxigraph binary, the
/// demo-agent, bundled datasets, and the built SPA) — assembled by
/// `desktop/scripts/bundle-backend.sh`. The backend is started as
/// `python3 -m asterism_api.local`, with env pointing every payload at the
/// bundle (console-script shebangs would break on relocation into the .app).
fn bundled_backend(app: &tauri::AppHandle) -> Option<BackendCmd> {
    let backend = app.path().resource_dir().ok()?.join("backend");
    let python = std::fs::read_dir(backend.join("uv-python"))
        .ok()?
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.path())
        .find(|path| {
            path.file_name()
                .map(|name| name.to_string_lossy().starts_with("cpython-"))
                .unwrap_or(false)
        })?
        .join("bin")
        .join("python3");
    if !python.is_file() {
        return None;
    }
    Some(BackendCmd {
        program: python,
        prefix_args: vec!["-m".into(), "asterism_api.local".into()],
        envs: vec![
            ("ASTERISM_UI_DIST".into(), backend.join("ui-dist")),
            ("ASTERISM_OXIGRAPH_BIN".into(), backend.join("oxigraph")),
            ("ASTERISM_DEMO_AGENT_DIR".into(), backend.join("demo-agent")),
            ("ASTERISM_DATASETS_ROOT".into(), backend.join("datasets")),
        ],
    })
}

/// Does this build carry its own backend? Decides which failure the user is
/// looking at: a distributed .app that lost (or cannot read) its payload —
/// answer: install it again — versus a repo checkout that was never set up.
fn is_bundled_build(app: &tauri::AppHandle) -> bool {
    app.path()
        .resource_dir()
        .map(|dir| dir.join("backend").is_dir())
        .unwrap_or(false)
}

fn checkout_backend() -> Option<BackendCmd> {
    find_launcher().map(|program| BackendCmd {
        program,
        prefix_args: vec![],
        envs: vec![],
    })
}

fn find_launcher() -> Option<PathBuf> {
    if let Ok(cmd) = std::env::var("ASTERISM_LOCAL_CMD") {
        let explicit = PathBuf::from(cmd);
        if explicit.is_file() {
            return Some(explicit);
        }
    }
    // Repo checkout: walk up from the executable (works for `tauri dev` and for
    // a .app built under desktop/src-tauri/target inside the repo).
    if let Ok(exe) = std::env::current_exe() {
        for ancestor in exe.ancestors() {
            let candidate = ancestor
                .join("api")
                .join(".venv")
                .join("bin")
                .join("asterism-local");
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    if let Ok(path) = std::env::var("PATH") {
        for dir in std::env::split_paths(&path) {
            let candidate = dir.join("asterism-local");
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

/// Signal the launcher's whole PROCESS GROUP (it is spawned as a group
/// leader): SIGTERM reaches asterism-local AND its Oxigraph / demo-agent
/// children directly, so cleanup does not depend on the launcher's own
/// shutdown finishing before an escalation — a plain SIGKILL of the launcher
/// was observed to orphan the demo-agent grandchild.
fn terminate(child: &mut Child) {
    #[cfg(unix)]
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGTERM);
    }
    #[cfg(not(unix))]
    let _ = child.kill();
    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline {
        if matches!(child.try_wait(), Ok(Some(_))) {
            return;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    #[cfg(unix)]
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGKILL);
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn stop_backend(app: &tauri::AppHandle) {
    let child = app.state::<Shell>().backend.lock().unwrap().take();
    if let Some(mut child) = child {
        terminate(&mut child);
    }
}

// ---------------------------------------------------------------------------
// Stop cards (the splash window is the surface; native dialog is the fallback)
// ---------------------------------------------------------------------------

fn set_status(app: &tauri::AppHandle, status: Status) {
    *app.state::<Shell>().status.lock().unwrap() = status;
}

/// Create the splash window, or bring it back if it was closed. Do NOT call
/// this from the main thread: it waits for the main thread to answer.
fn show_splash(app: &tauri::AppHandle) -> bool {
    let (tx, rx) = std::sync::mpsc::channel();
    let handle = app.clone();
    if app
        .run_on_main_thread(move || {
            let _ = tx.send(ensure_splash(&handle));
        })
        .is_err()
    {
        return false;
    }
    rx.recv_timeout(Duration::from_secs(10)).unwrap_or(false)
}

/// Main-thread half of `show_splash`.
fn ensure_splash(app: &tauri::AppHandle) -> bool {
    if let Some(window) = app.get_webview_window(SPLASH) {
        let _ = window.show();
        let _ = window.set_focus();
        return true;
    }
    WebviewWindowBuilder::new(app, SPLASH, WebviewUrl::App("index.html".into()))
        .title("Asterism")
        .inner_size(480.0, 380.0)
        .resizable(false)
        .center()
        .build()
        .is_ok()
}

fn close_splash(app: &tauri::AppHandle) {
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        if let Some(window) = handle.get_webview_window(SPLASH) {
            let _ = window.close();
        }
    });
}

/// Block until the splash window reports what the user chose. Treat the window
/// being closed as "やめる" so this can never spin forever.
fn wait_for_decision(app: &tauri::AppHandle) -> String {
    {
        let shell = app.state::<Shell>();
        *shell.decision.lock().unwrap() = None;
    }
    loop {
        if let Some(choice) = app.state::<Shell>().decision.lock().unwrap().take() {
            return choice;
        }
        if app.get_webview_window(SPLASH).is_none() {
            return "quit".to_string();
        }
        std::thread::sleep(Duration::from_millis(150));
    }
}

/// Draw a stop card and wait. The app stays alive until the user picks a way
/// out — no failure path ends by closing itself (K11).
fn halt(app: &tauri::AppHandle, status: Status) -> String {
    let fallback = format!("{}\n\n{}", t("fallback.failed"), status.detail);
    set_status(app, status);
    if !show_splash(app) {
        // Not even a window: a native dialog is all that is left, and OK is
        // then genuinely the only exit.
        app.dialog()
            .message(fallback)
            .kind(MessageDialogKind::Error)
            .title("Asterism")
            .blocking_show();
        return "quit".to_string();
    }
    wait_for_decision(app)
}

fn decide(app: &tauri::AppHandle, status: Status) -> Flow {
    match halt(app, status).as_str() {
        "retry" => Flow::Retry,
        _ => Flow::Quit,
    }
}

/// Non-fatal notice (unlike a stop card, nothing is waiting on it).
fn notify(app: &tauri::AppHandle, message: &str, kind: MessageDialogKind) {
    app.dialog()
        .message(message)
        .kind(kind)
        .title(t("update.title"))
        .blocking_show();
}

// ---------------------------------------------------------------------------
// Log: reachable, quotable, and mined for the few causes we can name
// ---------------------------------------------------------------------------

fn log_file(app: &tauri::AppHandle) -> Option<PathBuf> {
    app.state::<Shell>().log_path.lock().unwrap().clone()
}

fn log_tail(app: &tauri::AppHandle, lines: usize) -> String {
    let Some(path) = log_file(app) else {
        return String::new();
    };
    let Ok(text) = std::fs::read_to_string(&path) else {
        return String::new();
    };
    let kept: Vec<&str> = text
        .lines()
        .filter(|line| !line.trim().is_empty())
        .collect();
    kept[kept.len().saturating_sub(lines)..].join("\n")
}

fn append_log(app: &tauri::AppHandle, line: &str) {
    let Some(path) = log_file(app) else { return };
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
    {
        let _ = writeln!(file, "{line}");
    }
}

/// Name a cause ONLY when the log says so. Three patterns a person can act on;
/// anything else gets no cause line at all rather than a guess (the previous
/// "Oxigraph may not be installed" was always wrong for a bundled build).
fn plain_hint(tail: &str) -> &'static str {
    let text = tail.to_lowercase();
    if text.contains("address already in use")
        || text.contains("eaddrinuse")
        || text.contains("errno 48")
    {
        "hint.portInUse"
    } else if text.contains("permission denied")
        || text.contains("read-only file system")
        || text.contains("errno 13")
    {
        "hint.readOnly"
    } else if text.contains("no space left on device") || text.contains("errno 28") {
        "hint.diskFull"
    } else {
        ""
    }
}

fn open_url(url: &str) {
    #[cfg(target_os = "macos")]
    let _ = Command::new("open").arg(url).spawn();
    #[cfg(target_os = "windows")]
    let _ = Command::new("cmd").args(["/C", "start", "", url]).spawn();
    #[cfg(all(unix, not(target_os = "macos")))]
    let _ = Command::new("xdg-open").arg(url).spawn();
}

/// Show the file in the OS file manager (not open it): the point is that the
/// user can find it, attach it, or hand it to someone.
fn reveal_path(path: &Path) {
    #[cfg(target_os = "macos")]
    let _ = Command::new("open").arg("-R").arg(path).spawn();
    #[cfg(target_os = "windows")]
    let _ = Command::new("explorer")
        .arg(format!("/select,{}", path.display()))
        .spawn();
    #[cfg(all(unix, not(target_os = "macos")))]
    let _ = Command::new("xdg-open")
        .arg(path.parent().unwrap_or(path))
        .spawn();
}

fn reveal_log(app: &tauri::AppHandle) {
    let path = log_file(app).or_else(|| {
        app.path()
            .app_log_dir()
            .ok()
            .map(|dir| dir.join("backend.log"))
    });
    let Some(path) = path else { return };
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    reveal_path(&path);
}

/// What "内容をコピー" puts on the clipboard: enough for someone else to help.
fn copy_payload(app: &tauri::AppHandle) -> String {
    let detail = app.state::<Shell>().status.lock().unwrap().detail.clone();
    let mut out = format!("Asterism {}\n", app.package_info().version);
    if !detail.is_empty() {
        out.push_str(&detail);
        out.push('\n');
    }
    let tail = log_tail(app, 50);
    if !tail.is_empty() {
        out.push_str("\n--- backend.log (last 50 lines) ---\n");
        out.push_str(&tail);
    }
    out
}

// ---------------------------------------------------------------------------
// Commands the splash window calls
// ---------------------------------------------------------------------------

/// Tauri already refuses app commands from the remote SPA origin (only an
/// explicit `remote` capability opens them, and `grant_spa_update_ipc` opens
/// none). The label check makes that independent of ACL details.
fn splash_only(window: &tauri::WebviewWindow) -> Result<(), String> {
    if window.label() == SPLASH {
        Ok(())
    } else {
        Err("not allowed".to_string())
    }
}

#[tauri::command]
fn boot_status(app: tauri::AppHandle, window: tauri::WebviewWindow) -> Result<Status, String> {
    splash_only(&window)?;
    Ok(app.state::<Shell>().status.lock().unwrap().clone())
}

#[tauri::command]
fn boot_action(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    action: String,
) -> Result<Option<String>, String> {
    splash_only(&window)?;
    match action.as_str() {
        // Side effects: the card stays up.
        "log" => {
            reveal_log(&app);
            Ok(None)
        }
        "copy" => Ok(Some(copy_payload(&app))),
        "download" => {
            open_url(RELEASES_URL);
            Ok(None)
        }
        // Decisions: release whoever is blocked in `halt`.
        "retry" | "wait" | "quit" | "continue" | "close" => {
            *app.state::<Shell>().decision.lock().unwrap() = Some(action);
            Ok(None)
        }
        _ => Err("unknown action".to_string()),
    }
}

// ---------------------------------------------------------------------------
// Updates
// ---------------------------------------------------------------------------

/// Let the SPA drive updates from inside the window. The page is a remote
/// `http://127.0.0.1:<port>` origin, and Tauri grants remote origins nothing
/// unless a capability names them (the static `capabilities/default.json`
/// covers local pages only). This registers, at runtime, a capability scoped to
/// the ONE origin the window is about to load — port included, so it also holds
/// on the random-port fallback — that opens exactly:
///
/// - `updater:default`: check / download-and-install. Endpoints and the minisign
///   pubkey are baked into tauri.conf.json, so the page can neither point the
///   updater elsewhere nor skip signature verification;
/// - `process:allow-restart`: relaunch after install;
/// - `core:resources:allow-close`: release the update handle `check()` returns.
///
/// Nothing else — no shell, fs, dialog, window control, or shell command
/// reaches the page.
fn grant_spa_update_ipc(app: &tauri::AppHandle, port: u16) -> tauri::Result<()> {
    let capability = CapabilityBuilder::new("loopback-spa-updater")
        .local(false)
        .remote(format!("http://127.0.0.1:{port}"))
        .window(MAIN)
        .permission("updater:default")
        .permission("process:allow-restart")
        .permission("core:resources:allow-close");
    app.add_capability(capability)
}

fn collect_menu_items<R: tauri::Runtime>(
    items: Vec<MenuItemKind<R>>,
    ids: &[&str],
    out: &mut Vec<MenuItem<R>>,
) {
    for item in items {
        match item {
            MenuItemKind::Submenu(submenu) => {
                if let Ok(children) = submenu.items() {
                    collect_menu_items(children, ids, out);
                }
            }
            MenuItemKind::MenuItem(entry) => {
                if ids.iter().any(|id| entry.id() == *id) {
                    out.push(entry);
                }
            }
            _ => {}
        }
    }
}

/// Grey out "アップデートを確認…" while an update runs, so a second click
/// cannot start a second download. Must NOT run on the main thread: the menu
/// API hops there and waits.
fn set_update_menu_enabled(app: &tauri::AppHandle, enabled: bool) {
    let Some(menu) = app.menu() else { return };
    let Ok(items) = menu.items() else { return };
    let mut found = Vec::new();
    collect_menu_items(items, &["check_update", "check_update_help"], &mut found);
    for item in found {
        let _ = item.set_enabled(enabled);
    }
}

enum UpdateFlow {
    Done,
    Retry,
}

/// Native update flow behind the menu item — the fallback that needs no page:
/// checks the release `latest.json` against the bundled version, offers a
/// native dialog, then downloads (with progress in the splash window),
/// installs, and relaunches. The everyday surface is the SPA banner (see the
/// module doc); this stays for when the page cannot help (blank window, IPC
/// refused, a broken build). Always user-initiated, so every outcome — including
/// "already up to date" and errors — is reported.
async fn check_for_updates(app: tauri::AppHandle) {
    if app.state::<Shell>().updating.swap(true, Ordering::SeqCst) {
        return; // already downloading: the menu item is greyed out too
    }
    set_update_menu_enabled(&app, false);
    while let UpdateFlow::Retry = update_once(&app).await {}
    set_update_menu_enabled(&app, true);
    app.state::<Shell>().updating.store(false, Ordering::SeqCst);
}

async fn update_once(app: &tauri::AppHandle) -> UpdateFlow {
    let updater = match app.updater() {
        Ok(updater) => updater,
        Err(err) => {
            eprintln!("asterism-desktop: updater unavailable: {err}");
            notify(app, t("update.unavailable"), MessageDialogKind::Error);
            return UpdateFlow::Done;
        }
    };
    let update = match updater.check().await {
        Ok(Some(update)) => update,
        Ok(None) => {
            notify(app, t("update.upToDate"), MessageDialogKind::Info);
            return UpdateFlow::Done;
        }
        Err(err) => {
            eprintln!("asterism-desktop: update check failed: {err}");
            notify(app, t("update.checkFailed"), MessageDialogKind::Error);
            return UpdateFlow::Done;
        }
    };

    let message = format!(
        "{}\n\n{}",
        update_available_line(&update.version, &update.current_version),
        t("update.notes")
    );
    let accepted = app
        .dialog()
        .message(message)
        .title(t("update.title"))
        .buttons(MessageDialogButtons::OkCancelCustom(
            t("update.install").to_string(),
            t("update.later").to_string(),
        ))
        .blocking_show();
    if !accepted {
        return UpdateFlow::Done;
    }

    // Show where the time is going: the download can be hundreds of MB, and the
    // window otherwise looks frozen until the app restarts by itself.
    set_status(app, updating_status(None));
    let splash_app = app.clone();
    let _ = tauri::async_runtime::spawn_blocking(move || show_splash(&splash_app)).await;

    let progress_app = app.clone();
    let mut downloaded: u64 = 0;
    let outcome = update
        .download_and_install(
            move |chunk, total| {
                downloaded += chunk as u64;
                let percent = total
                    .filter(|bytes| *bytes > 0)
                    .map(|bytes| (downloaded.saturating_mul(100) / bytes).min(100) as u8);
                set_status(&progress_app, updating_status(percent));
            },
            || {},
        )
        .await;

    match outcome {
        Ok(()) => app.restart(),
        Err(err) => {
            eprintln!("asterism-desktop: update install failed: {err}");
            let card = Status::card(
                "card.updateFailed",
                err.to_string(),
                &["retry", "download", "close"],
            );
            match halt_async(app, card).await.as_deref() {
                Some("retry") => UpdateFlow::Retry,
                Some(_) => {
                    close_splash(app);
                    UpdateFlow::Done
                }
                // No window at all — say it the only way left.
                None => {
                    notify(app, t("update.failed"), MessageDialogKind::Error);
                    UpdateFlow::Done
                }
            }
        }
    }
}

fn updating_status(percent: Option<u8>) -> Status {
    Status {
        percent,
        ..Status::working("updating")
    }
}

/// `halt` for the async update path. `None` means the card could not be shown.
async fn halt_async(app: &tauri::AppHandle, status: Status) -> Option<String> {
    set_status(app, status);
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        if !show_splash(&handle) {
            return None;
        }
        Some(wait_for_decision(&handle))
    })
    .await
    .unwrap_or(None)
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

fn boot(app: tauri::AppHandle) {
    loop {
        match boot_once(&app) {
            Flow::Done => return,
            Flow::Retry => {
                // A retry after a timeout can leave a live child holding the
                // port: stop it (and take a fresh port) before going again.
                set_status(&app, Status::working("stopping"));
                stop_backend(&app);
            }
            Flow::Quit => {
                app.exit(1);
                return;
            }
        }
    }
}

fn boot_once(app: &tauri::AppHandle) -> Flow {
    set_status(app, Status::working("starting"));
    let bundled = is_bundled_build(app);

    let Some(backend) = bundled_backend(app).or_else(checkout_backend) else {
        // A distributed .app that cannot find its own payload is not missing a
        // dev setup — it is damaged, and the fix is to install it again.
        return if bundled {
            let where_ = app
                .path()
                .resource_dir()
                .map(|dir| dir.join("backend").display().to_string())
                .unwrap_or_else(|_| "resource dir".to_string());
            decide(
                app,
                Status::card(
                    "card.notFound",
                    format!("no usable runtime under {where_}"),
                    &["download", "quit"],
                ),
            )
        } else {
            decide(
                app,
                Status::card(
                    "card.notFoundDev",
                    DEV_SETUP.to_string(),
                    &["retry", "quit"],
                )
                .open_detail(),
            )
        };
    };

    let (port, attach, fallback) = match resolve_port() {
        PortUse::Fresh(port) => (port, false, false),
        PortUse::Attach => (PREFERRED_PORT, true, false),
        PortUse::Fallback(port) => {
            let card = Status::card(
                "card.portBusy",
                format!("127.0.0.1:{PREFERRED_PORT} is taken by another program; using {port} for this session"),
                &["continue", "quit"],
            );
            match halt(app, card).as_str() {
                "continue" => {
                    set_status(app, Status::working("starting"));
                    (port, false, true)
                }
                _ => return Flow::Quit,
            }
        }
    };

    // Before the window exists: the grant is keyed by the origin the window
    // will load. Failure is not fatal — the menu item still updates the app —
    // but the backend is told, so the SPA can point at the menu instead of
    // showing an update banner that can never work.
    let ipc_granted = match grant_spa_update_ipc(app, port) {
        Ok(()) => true,
        Err(err) => {
            eprintln!("asterism-desktop: could not grant updater IPC to the SPA: {err}");
            false
        }
    };

    let log_path = app.path().app_log_dir().ok().map(|dir| {
        let _ = std::fs::create_dir_all(&dir);
        dir.join("backend.log")
    });
    *app.state::<Shell>().log_path.lock().unwrap() = log_path.clone();

    // `attach` means an Asterism is already serving that port: open its window
    // rather than starting a second backend on the same data.
    if !attach {
        if let Err(flow) = spawn_backend(app, &backend, port, ipc_granted, log_path.as_deref()) {
            return flow;
        }
        if let Err(flow) = wait_ready(app, port, bundled) {
            return flow;
        }
    }

    open_main(app, port, fallback)
}

/// Start the backend as a process-group leader, output appended to backend.log.
fn spawn_backend(
    app: &tauri::AppHandle,
    backend: &BackendCmd,
    port: u16,
    ipc_granted: bool,
    log_path: Option<&Path>,
) -> Result<(), Flow> {
    let (stdout, stderr) = match log_path {
        Some(path) => match std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
        {
            Ok(file) => (
                file.try_clone()
                    .map(Stdio::from)
                    .unwrap_or_else(|_| Stdio::null()),
                Stdio::from(file),
            ),
            Err(_) => (Stdio::null(), Stdio::null()),
        },
        None => (Stdio::null(), Stdio::null()),
    };

    let mut command = Command::new(&backend.program);
    command
        .args(&backend.prefix_args)
        .args(["--no-browser", "--port", &port.to_string()])
        .stdin(Stdio::null())
        .stdout(stdout)
        .stderr(stderr);
    for (key, value) in &backend.envs {
        command.env(key, value);
    }
    // Tell the backend which build it belongs to: `/api/instance` relays it, so
    // the SPA shows the version (settings → About) and knows it is running
    // inside the desktop app — without any IPC beyond the updater grant above.
    command.env(
        "ASTERISM_APP_VERSION",
        app.package_info().version.to_string(),
    );
    // …and whether the in-page update banner can work at all, so the settings
    // screen can point at the menu bar instead of offering a button that will
    // fail every time.
    command.env("ASTERISM_UPDATER_IPC", if ipc_granted { "1" } else { "0" });
    // New process group with pgid = launcher pid: the Oxigraph / demo-agent
    // grandchildren inherit it, so terminate() can signal the whole tree.
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    match command.spawn() {
        Ok(child) => {
            *app.state::<Shell>().backend.lock().unwrap() = Some(child);
            Ok(())
        }
        Err(err) => Err(decide(
            app,
            Status::card(
                "card.startFailed",
                format!("{err}\n{}", backend.program.display()),
                &["retry", "log", "quit"],
            ),
        )),
    }
}

/// Wait for the backend to answer. Two things interrupt the wait: the child
/// dying (the log can often name why) and the wait growing long — which is a
/// question for the user, not a reason to quit on their behalf. A first launch
/// unpacks a ~370 MB bundle and gets scanned by Gatekeeper, so "slow" is not
/// the same as "broken".
fn wait_ready(app: &tauri::AppHandle, port: u16, bundled: bool) -> Result<(), Flow> {
    let mut deadline = Instant::now() + READY_TIMEOUT;
    loop {
        if http_ready(port) {
            return Ok(());
        }
        let exited = {
            let state = app.state::<Shell>();
            let mut guard = state.backend.lock().unwrap();
            let status = match guard.as_mut() {
                Some(child) => child.try_wait().ok().flatten(),
                None => None,
            };
            if status.is_some() {
                guard.take();
            }
            status.map(|status| status.to_string())
        };
        if let Some(status) = exited {
            let mut card = Status::card(
                "card.stopped",
                format!("exit: {status}\n\n{}", log_tail(app, 50)),
                &["retry", "log", "copy", "quit"],
            )
            .with_hint(plain_hint(&log_tail(app, 5)));
            if !bundled {
                // A repo checkout is read by a developer: leave the technical
                // text in front, where it is the fastest answer.
                card = card.open_detail();
            }
            return Err(decide(app, card));
        }
        if Instant::now() > deadline {
            let card = Status::card(
                "card.slow",
                format!(
                    "no answer from 127.0.0.1:{port} after {}s",
                    READY_TIMEOUT.as_secs()
                ),
                &["wait", "log", "quit"],
            );
            match halt(app, card).as_str() {
                "wait" => {
                    set_status(app, Status::working("starting"));
                    deadline = Instant::now() + READY_TIMEOUT;
                }
                "retry" => return Err(Flow::Retry),
                _ => return Err(Flow::Quit),
            }
        }
        std::thread::sleep(Duration::from_millis(200));
    }
}

fn open_main(app: &tauri::AppHandle, port: u16, fallback: bool) -> Flow {
    // The mark tells the SPA it is running on a substitute origin, so it can
    // explain why the settings look empty instead of leaving them lost.
    let url = if fallback {
        format!("http://127.0.0.1:{port}/?port_fallback=1")
    } else {
        format!("http://127.0.0.1:{port}/")
    };
    match build_main_window(app, &url) {
        Ok(()) => {
            // Only now: while the main window is not up, the splash is the
            // only thing the user has.
            close_splash(app);
            Flow::Done
        }
        Err(err) => {
            eprintln!("asterism-desktop: could not open the window: {err}");
            append_log(
                app,
                &format!("asterism-desktop: could not open the window: {err}"),
            );
            decide(
                app,
                Status::card("card.windowFailed", err, &["retry", "log", "quit"]),
            )
        }
    }
}

fn build_main_window(app: &tauri::AppHandle, url: &str) -> Result<(), String> {
    let (tx, rx) = std::sync::mpsc::channel();
    let handle = app.clone();
    let url = url.to_string();
    app.run_on_main_thread(move || {
        let result = match url.parse() {
            Ok(parsed) => WebviewWindowBuilder::new(&handle, MAIN, WebviewUrl::External(parsed))
                .title("Asterism")
                .inner_size(1280.0, 800.0)
                .min_inner_size(900.0, 600.0)
                .center()
                .build()
                .map(|_| ())
                .map_err(|err| err.to_string()),
            Err(err) => Err(format!("{err}")),
        };
        let _ = tx.send(result);
    })
    .map_err(|err| err.to_string())?;
    rx.recv_timeout(Duration::from_secs(30))
        .map_err(|err| err.to_string())?
}

// ---------------------------------------------------------------------------
// Menu
// ---------------------------------------------------------------------------

/// macOS-standard menu (so webview copy/paste keeps working) with "Check for
/// Updates" in the app submenu, plus a Help menu on every platform: when the
/// page cannot help, the menu bar is the only surface left, so the log and the
/// getting-started page have to be reachable from it.
fn build_menu(handle: &tauri::AppHandle) -> tauri::Result<tauri::menu::Menu<tauri::Wry>> {
    let check = MenuItemBuilder::with_id("check_update", t("menu.checkUpdate")).build(handle)?;
    let open_log = MenuItemBuilder::with_id("open_log", t("menu.openLog")).build(handle)?;
    let guide = MenuItemBuilder::with_id("open_guide", t("menu.guide")).build(handle)?;
    #[cfg(target_os = "macos")]
    {
        // A second item with its own id: one MenuItem cannot sit in two menus.
        let check_help =
            MenuItemBuilder::with_id("check_update_help", t("menu.checkUpdate")).build(handle)?;
        let app_menu = SubmenuBuilder::new(handle, "Asterism")
            .about(None)
            .item(&check)
            .separator()
            .services()
            .separator()
            .hide()
            .hide_others()
            .show_all()
            .separator()
            .quit()
            .build()?;
        let edit_menu = SubmenuBuilder::new(handle, t("menu.edit"))
            .undo()
            .redo()
            .separator()
            .cut()
            .copy()
            .paste()
            .select_all()
            .build()?;
        let window_menu = SubmenuBuilder::new(handle, t("menu.window"))
            .minimize()
            .separator()
            .close_window()
            .build()?;
        let help_menu = SubmenuBuilder::new(handle, t("menu.help"))
            .item(&open_log)
            .item(&guide)
            .separator()
            .item(&check_help)
            .build()?;
        MenuBuilder::new(handle)
            .items(&[&app_menu, &edit_menu, &window_menu, &help_menu])
            .build()
    }
    #[cfg(not(target_os = "macos"))]
    {
        let help_menu = SubmenuBuilder::new(handle, t("menu.help"))
            .item(&open_log)
            .item(&guide)
            .separator()
            .item(&check)
            .build()?;
        MenuBuilder::new(handle).items(&[&help_menu]).build()
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .manage(Shell::default())
        .invoke_handler(tauri::generate_handler![boot_status, boot_action])
        .menu(build_menu)
        .on_menu_event(|app, event| {
            let id = event.id();
            if id == "check_update" || id == "check_update_help" {
                tauri::async_runtime::spawn(check_for_updates(app.clone()));
            } else if id == "open_log" {
                reveal_log(app);
            } else if id == "open_guide" {
                open_url(GUIDE_URL);
            }
        })
        .setup(|app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || boot(handle));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| match event {
            // Quitting is not instant: the backend group gets SIGTERM and up to
            // 10 s to close its store. Hold the exit so that cleanup finishes,
            // and if it is slow enough to be noticed, say what is happening
            // instead of leaving a window-less app in the Dock.
            RunEvent::ExitRequested { api, .. } => {
                let already = app
                    .state::<Shell>()
                    .shutting_down
                    .swap(true, Ordering::SeqCst);
                if already {
                    return;
                }
                let child = app.state::<Shell>().backend.lock().unwrap().take();
                let Some(mut child) = child else { return };
                api.prevent_exit();
                set_status(app, Status::working("quitting"));
                let watcher = app.clone();
                std::thread::spawn(move || {
                    std::thread::sleep(Duration::from_secs(2));
                    if !watcher.state::<Shell>().terminated.load(Ordering::SeqCst) {
                        show_splash(&watcher);
                    }
                });
                let handle = app.clone();
                std::thread::spawn(move || {
                    terminate(&mut child);
                    handle
                        .state::<Shell>()
                        .terminated
                        .store(true, Ordering::SeqCst);
                    handle.exit(0);
                });
            }
            // Safety net for exits that skip the request (e.g. `restart()`).
            RunEvent::Exit => {
                if let Some(mut child) = app.state::<Shell>().backend.lock().unwrap().take() {
                    terminate(&mut child);
                }
            }
            _ => {}
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plain_hint_only_names_causes_the_log_states() {
        assert_eq!(
            plain_hint("ERROR: [Errno 48] Address already in use"),
            "hint.portInUse"
        );
        assert_eq!(
            plain_hint("PermissionError: [Errno 13] Permission denied: '/x'"),
            "hint.readOnly"
        );
        assert_eq!(
            plain_hint("OSError: [Errno 28] No space left on device"),
            "hint.diskFull"
        );
        // No guessing: an unknown failure gets no cause line at all.
        assert_eq!(plain_hint("Traceback (most recent call last)"), "");
        assert_eq!(plain_hint(""), "");
    }

    #[test]
    fn cards_keep_the_technical_text_but_fold_it_away() {
        let card = Status::card("card.stopped", "exit: 1".to_string(), &["retry", "quit"]);
        assert_eq!(card.phase, "card");
        assert_eq!(card.detail, "exit: 1");
        assert!(!card.detail_open);
        assert_eq!(card.actions, vec!["retry", "quit"]);
    }
}
