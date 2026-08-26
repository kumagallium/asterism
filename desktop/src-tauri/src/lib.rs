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

mod settings;

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde::Serialize;
use settings::StorageNotice;
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
    /// The port `boot_once` actually settled on. `restart_backend` rebinds
    /// this exact port rather than picking a fresh one: the origin the SPA
    /// runs at is `http://127.0.0.1:<port>`, and browser localStorage
    /// (registered models, remembered keys — see `PREFERRED_PORT`'s comment)
    /// is keyed by origin, so hopping ports on restart would silently wipe it.
    port: Mutex<Option<u16>>,
    /// pgid of the most recently spawned backend (= its own pid, since it is
    /// started as a process-group leader — see `terminate`). Kept even after
    /// the `Child` itself has been reaped, because a backend that died on its
    /// own (e.g. an external SIGTERM, as happened in production) leaves this
    /// as the only handle left to the orphaned Oxigraph / demo-agent
    /// grandchildren — `restart_backend` needs it to clean the group up
    /// before starting a new one on the same port.
    last_pgid: Mutex<Option<i32>>,
    /// Guards `restart_backend` against a second click landing mid-restart.
    restarting: AtomicBool,
    /// Set once `open_main` has actually opened the MAIN window (`Flow::Done`),
    /// cleared again whenever `boot()` re-enters `boot_once` on `Flow::Retry`.
    /// `restart_backend` has nothing to restart — and nowhere to reload —
    /// before this is true: the backend `boot_once` is waiting on lives in the
    /// SAME `Shell.backend` slot `restart_backend` would `take()` and kill, so
    /// a restart that lands mid-boot steals the very process boot is polling
    /// for readiness. The native menu item is also disabled/enabled in step
    /// with this flag (see `set_restart_menu_enabled`), so the guard below is
    /// a backstop, not the only line of defense.
    booted: AtomicBool,
    /// Whether `grant_spa_update_ipc` succeeded for the origin this launch is
    /// serving. The backend is told (`ASTERISM_UPDATER_IPC`) so the SPA knows
    /// whether it can offer an in-window update at all. A restart keeps the
    /// same origin, so it must pass the SAME answer on — claiming a grant that
    /// never happened would put an update banner in front of the user that
    /// could not work.
    ipc_granted: AtomicBool,
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
    LANG.get_or_init(|| {
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
        "menu.restartBackend" => {
            if ja {
                "バックエンドを再起動"
            } else {
                "Restart Backend"
            }
        }
        // Shown as a native dialog: the page is presumed dead when this
        // fires, so there is no banner or card to draw it into instead.
        "menu.restartBackendFailed" => {
            if ja {
                "バックエンドを再起動できませんでした。ログを確認してください。"
            } else {
                "Couldn't restart the backend. Check the log for details."
            }
        }
        // Shown instead of `menu.restartBackendFailed` specifically when the
        // new backend simply has not answered yet within the timeout — it is
        // still running and may finish starting on its own (see the comment
        // in `restart_backend_blocking`'s timeout branch).
        "menu.restartBackendSlow" => {
            if ja {
                "バックエンドがまだ応答していません。起動処理が続いている可能性があります。\
                 しばらく待っても直らない場合は、ログを確認してください。"
            } else {
                "The backend hasn't answered yet — it may still be starting up. \
                 If this doesn't clear up after a while, check the log for details."
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

/// SIGTERM a whole process GROUP by pgid, escalating to SIGKILL after 10s if
/// anything is still alive under it. Shared by `restart_backend_blocking` and
/// `spawn_watchdog`'s unexpected-exit cleanup — both are cleaning up a launcher
/// that already exited (so there is no `Child` left to `terminate()`), only
/// the pgid it left behind in `Shell::last_pgid`.
#[cfg(unix)]
fn kill_process_group(pgid: i32) {
    unsafe {
        libc::kill(-pgid, libc::SIGTERM);
    }
    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline && unsafe { libc::kill(-pgid, 0) } == 0 {
        std::thread::sleep(Duration::from_millis(100));
    }
    if unsafe { libc::kill(-pgid, 0) } == 0 {
        unsafe {
            libc::kill(-pgid, libc::SIGKILL);
        }
    }
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
    // Some cards already fold the same tail into `detail`; pasting it twice
    // only makes the report someone else has to read longer.
    if !tail.is_empty() && !detail.contains(&tail) {
        out.push_str("\n--- backend.log (last 50 lines) ---\n");
        out.push_str(&tail);
    }
    out
}

// ---------------------------------------------------------------------------
// Commands the splash window calls
// ---------------------------------------------------------------------------

/// Tauri refuses app commands from the remote SPA origin unless a capability
/// names them, and `grant_spa_update_ipc` names only the two storage-location
/// commands — never these. The label check makes that independent of ACL
/// details.
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

/// Let the SPA drive updates — and the storage-location setting — from
/// inside the window. The page is a remote `http://127.0.0.1:<port>` origin,
/// and Tauri grants remote origins nothing unless a capability names them
/// (the static `capabilities/default.json` covers local pages only — that is
/// the splash window, never this one). This registers, at runtime, a
/// capability scoped to the ONE origin the window is about to load — port
/// included, so it also holds on the random-port fallback — that opens
/// exactly:
///
/// - `updater:default`: check / download-and-install. Endpoints and the minisign
///   pubkey are baked into tauri.conf.json, so the page can neither point the
///   updater elsewhere nor skip signature verification;
/// - `process:allow-restart`: relaunch after install;
/// - `core:resources:allow-close`: release the update handle `check()` returns;
/// - `allow-get-data-home-override` / `allow-set-data-home-override`
///   (app-defined, see `permissions/data-home.toml`): read/save the
///   storage-location setting (ADR `app-data-on-disk.md` D4). Save-only —
///   the SPA cannot make the shell touch the filesystem beyond this file;
///   `set` can additionally schedule a data move for the next boot, but the
///   move itself only ever runs from `boot()`, never from the IPC handler;
/// - `allow-get-storage-notice` / `allow-clear-storage-notice`: read (and
///   dismiss) the outcome of the most recent boot-time move;
/// - `dialog:allow-open`: lets the storage-location setting show a native
///   folder picker. Only `open` — `save`/`message`/etc. stay closed (#377's
///   "grant only the IPC that is needed" policy);
/// - `allow-restart-backend` (app-defined, see `permissions/backend.toml`):
///   stop and start the backend again on this same port, for when it has
///   died silently underneath the page.
///
/// Nothing else — no shell, fs, or window control reaches the page.
fn grant_spa_update_ipc(app: &tauri::AppHandle, port: u16) -> tauri::Result<()> {
    let capability = CapabilityBuilder::new("loopback-spa-updater")
        .local(false)
        .remote(format!("http://127.0.0.1:{port}"))
        .window(MAIN)
        .permission("updater:default")
        .permission("process:allow-restart")
        .permission("core:resources:allow-close")
        .permission("allow-get-data-home-override")
        .permission("allow-set-data-home-override")
        .permission("allow-get-storage-notice")
        .permission("allow-clear-storage-notice")
        .permission("dialog:allow-open")
        .permission("allow-restart-backend");
    app.add_capability(capability)
}

// ---------------------------------------------------------------------------
// Commands the SPA calls (storage location)
// ---------------------------------------------------------------------------

/// Current storage-location override, if any (ADR `app-data-on-disk.md` D4).
/// `None` means "use the backend's own default data dir".
#[tauri::command]
fn get_data_home_override(app: tauri::AppHandle) -> Option<String> {
    settings::read_data_home_override(&app)
}

/// Save (or, with `path: None`, clear) the storage-location override.
/// Save-only: does not restart the backend, and does not touch the
/// filesystem beyond the settings file itself. The new location is picked
/// up on the next launch (same as Graphium).
///
/// `move_from` (JS `moveFrom`), when given alongside `Some(path)`,
/// additionally schedules "move the existing data at this absolute path
/// into `path` on the next launch, before the sidecar starts" — see
/// `perform_pending_move` in this module, invoked from `boot()`. The caller
/// (SPA) reads the current data home from `/api/appdata/info` and passes it
/// as `moveFrom`; this command itself never inspects or moves anything.
#[tauri::command]
fn set_data_home_override(app: tauri::AppHandle, path: Option<String>, move_from: Option<String>) {
    settings::write_data_home_override(&app, path, move_from);
}

/// Outcome of the most recent boot-time data move, if any (ADR
/// `app-data-on-disk.md` D4 follow-up). `None` once the SPA has cleared it,
/// or if no move has ever been attempted.
#[tauri::command]
fn get_storage_notice(app: tauri::AppHandle) -> Option<StorageNotice> {
    settings::read_storage_notice(&app)
}

/// Dismiss the stored move-outcome notice, once the SPA has shown it to the
/// user.
#[tauri::command]
fn clear_storage_notice(app: tauri::AppHandle) {
    settings::clear_storage_notice(&app);
}

/// Stop the backend and start it again on the same loopback port — the
/// command behind both the "restart" menu item and, via
/// `allow-restart-backend`, the page itself when it detects the backend is
/// gone. On success, returns the port so the caller knows where to reload
/// (in practice always the same port it was already on: see below).
///
/// Deliberately narrow, mirroring Graphium's own restart command: this never
/// tries a different port if the original one will not come free. The
/// update-IPC capability granted in `grant_spa_update_ipc` is scoped to one
/// exact origin (`http://127.0.0.1:<port>`) and is not re-granted here, so a
/// restart that lands on a different port would leave the page without that
/// grant — silently breaking the update banner — which makes "moved to
/// another port" worse than "failed, try again", not better.
#[tauri::command]
async fn restart_backend(app: tauri::AppHandle) -> Result<u16, String> {
    // The SPA only ever shows the reason as text, so the typed distinction
    // (`RestartError`) is flattened here; the native menu path keeps the type
    // because it picks a different sentence for each case.
    restart_backend_typed(app)
        .await
        .map_err(|err| err.detail().to_string())
}

async fn restart_backend_typed(app: tauri::AppHandle) -> Result<u16, RestartError> {
    match tauri::async_runtime::spawn_blocking(move || restart_backend_blocking(&app)).await {
        Ok(result) => result,
        Err(err) => Err(RestartError::Failed(err.to_string())),
    }
}

/// The double-click guard, pulled out from `restart_backend_blocking` so it
/// can be tested against a bare `AtomicBool` without a `Shell`/`AppHandle` in
/// the loop. `true` means the caller now owns the flag and must eventually
/// release it (see `ClearOnDrop` at the call site); `false` means someone
/// else already does and this call must not proceed.
fn try_acquire_restart_guard(flag: &AtomicBool) -> bool {
    !flag.swap(true, Ordering::SeqCst)
}

/// Whether `restart_backend_blocking` may proceed at all — pulled out from it
/// so this can be tested against a bare `AtomicBool` the same way
/// `try_acquire_restart_guard` is. `false` before `open_main` has opened the
/// MAIN window (see `Shell::booted`): `boot_once` is still spawning/waiting
/// on the very `Shell.backend` a restart would `take()` and kill.
fn restart_allowed(booted: &AtomicBool) -> bool {
    booted.load(Ordering::SeqCst)
}

fn restart_backend_blocking(app: &tauri::AppHandle) -> Result<u16, RestartError> {
    let shell = app.state::<Shell>();
    if !try_acquire_restart_guard(&shell.restarting) {
        return Err(RestartError::Failed(
            "a restart is already in progress".to_string(),
        ));
    }
    // A `Drop` guard, rather than resetting the flag before every `return`
    // below, so the flag comes back down no matter which path exits.
    struct ClearOnDrop<'a>(&'a AtomicBool);
    impl Drop for ClearOnDrop<'_> {
        fn drop(&mut self) {
            self.0.store(false, Ordering::SeqCst);
        }
    }
    let _guard = ClearOnDrop(&shell.restarting);

    if shell.updating.load(Ordering::SeqCst) {
        return Err(RestartError::Failed("an update is in progress".to_string()));
    }
    if shell.shutting_down.load(Ordering::SeqCst) {
        return Err(RestartError::Failed("the app is shutting down".to_string()));
    }
    // `boot_once` has not opened the MAIN window yet: it is still spawning (or
    // waiting on) the very `Shell.backend` this function would `take()` and
    // kill. The native menu item is disabled for the same window (see
    // `set_restart_menu_enabled`), but the menu item and this command are not
    // the same lock, so this check is the actual guard.
    if !restart_allowed(&shell.booted) {
        return Err(RestartError::Failed(
            "the app has not finished starting yet".to_string(),
        ));
    }

    append_log(app, "restart_backend: requested");

    // Reap whatever this process is still tracking…
    stop_backend(app);

    // …and separately signal the whole process GROUP by pgid. This matters
    // when the child died on its own (the production incident this exists
    // for: an external SIGTERM left the launcher's Oxigraph and demo-agent
    // children orphaned but alive) — `stop_backend` above found nothing to
    // reap, but the group is still holding the port. Kept here as a backstop
    // even though the watchdog (`spawn_watchdog`) now does the same cleanup
    // on an unexpected exit within ~2s: this covers the window before the
    // watchdog notices, e.g. a user clicking restart within that window.
    #[cfg(unix)]
    if let Some(pgid) = shell.last_pgid.lock().unwrap().take() {
        kill_process_group(pgid);
    }

    let Some(port) = *shell.port.lock().unwrap() else {
        let err = "no backend port on record".to_string();
        append_log(app, &format!("restart_backend: failed: {err}"));
        return Err(RestartError::Failed(err));
    };

    // The OS can hold a just-closed socket in TIME_WAIT for a moment, so the
    // port freeing up is not instant even once the group above is gone.
    let bind_deadline = Instant::now() + Duration::from_secs(10);
    loop {
        match TcpListener::bind(("127.0.0.1", port)) {
            Ok(listener) => {
                drop(listener);
                break;
            }
            Err(_) if Instant::now() > bind_deadline => {
                if asterism_at(port) {
                    // Another Asterism is already serving this port — most
                    // likely a second launch raced this one. Nothing to
                    // restart; the caller just needs to reload against it.
                    append_log(
                        app,
                        "restart_backend: another Asterism already holds the port",
                    );
                    return Ok(port);
                }
                let err = format!("port {port} is held by another program");
                append_log(app, &format!("restart_backend: failed: {err}"));
                return Err(RestartError::Failed(err));
            }
            Err(_) => std::thread::sleep(Duration::from_millis(200)),
        }
    }

    let Some(backend) = bundled_backend(app).or_else(checkout_backend) else {
        let err = "no backend runtime found".to_string();
        append_log(app, &format!("restart_backend: failed: {err}"));
        return Err(RestartError::Failed(err));
    };

    let log_path = log_file(app);
    // The port is unchanged, so the capability `grant_spa_update_ipc`
    // registered for this origin at boot still covers the new child — there is
    // nothing to re-grant. Pass on whatever that grant actually returned at
    // boot, rather than assuming it succeeded.
    let ipc_granted = shell.ipc_granted.load(Ordering::SeqCst);
    if let Err(err) = spawn_backend_quiet(app, &backend, port, ipc_granted, log_path.as_deref()) {
        append_log(app, &format!("restart_backend: failed to spawn: {err}"));
        return Err(RestartError::Failed(err));
    }

    match wait_ready_quiet(app, port, Duration::from_secs(60)) {
        Ok(()) => {
            append_log(
                app,
                &format!("restart_backend: succeeded, backend answering on {port}"),
            );
            Ok(port)
        }
        Err(not_ready) => {
            // A child that is merely SLOW is deliberately NOT torn down here:
            // `Shell.backend` still holds it, it may only need a little longer
            // (the same Gatekeeper-scan slowness `READY_TIMEOUT`'s doc talks
            // about applies here too, not just on first launch), the SPA polls
            // `/health` every few seconds and will drop its own banner the
            // moment the child answers, and the NEXT `restart_backend` call
            // will find it in `Shell.backend` and `stop_backend` it properly
            // before starting a fresh one. That case is "let it keep trying,
            // tell the user it is not ready yet" — not "give up".
            //
            // A child that EXITED is a different answer: nothing is coming, so
            // it is reported as a plain failure.
            let slow = matches!(not_ready, NotReady::Slow(_));
            let detail = not_ready.detail();
            append_log(
                app,
                &format!("restart_backend: backend did not become ready: {detail}"),
            );
            Err(if slow {
                RestartError::Slow(detail)
            } else {
                RestartError::Failed(detail)
            })
        }
    }
}

/// Recursively copy `from` into `to` (which must not yet exist), preserving
/// symlinks as symlinks (not following them) and file permissions. Used as
/// the cross-volume fallback when `std::fs::rename` cannot do an atomic
/// same-volume move (ADR `app-data-on-disk.md` D4 follow-up).
fn copy_dir_recursive(from: &Path, to: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(to)?;
    if let Ok(meta) = std::fs::metadata(from) {
        let _ = std::fs::set_permissions(to, meta.permissions());
    }
    for entry in std::fs::read_dir(from)? {
        let entry = entry?;
        let file_type = entry.file_type()?;
        let dest = to.join(entry.file_name());
        if file_type.is_symlink() {
            copy_symlink(&entry.path(), &dest)?;
        } else if file_type.is_dir() {
            copy_dir_recursive(&entry.path(), &dest)?;
        } else {
            std::fs::copy(entry.path(), &dest)?;
        }
    }
    Ok(())
}

#[cfg(unix)]
fn copy_symlink(src: &Path, dest: &Path) -> std::io::Result<()> {
    let target = std::fs::read_link(src)?;
    std::os::unix::fs::symlink(target, dest)
}

#[cfg(not(unix))]
fn copy_symlink(src: &Path, dest: &Path) -> std::io::Result<()> {
    // Windows symlinks need elevated privileges to create; fall back to
    // copying whatever the link points at instead of failing the move.
    if src.is_dir() {
        copy_dir_recursive(src, dest)
    } else {
        std::fs::copy(src, dest).map(|_| ())
    }
}

/// Files the OS drops into folders by itself. A folder the user just created
/// in the macOS picker is "empty" to them even with one of these in it, so
/// these must not block a move.
const OS_CRUFT: &[&str] = &[".DS_Store", ".localized", "Thumbs.db", "desktop.ini"];

fn dir_has_entries(dir: &Path) -> bool {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return false;
    };
    entries
        .flatten()
        .any(|e| !OS_CRUFT.contains(&e.file_name().to_string_lossy().as_ref()))
}

/// An absolute-ish path for comparison, working even when the path does not
/// exist yet (canonicalize the deepest existing ancestor, keep the rest).
fn resolve_for_compare(path: &Path) -> PathBuf {
    if let Ok(real) = path.canonicalize() {
        return real;
    }
    match (path.parent(), path.file_name()) {
        (Some(parent), Some(name)) => resolve_for_compare(parent).join(name),
        _ => path.to_path_buf(),
    }
}

fn move_failed(from: &str, to: &str, detail: impl Into<String>) -> StorageNotice {
    StorageNotice {
        kind: "failed".into(),
        from: from.to_string(),
        to: to.to_string(),
        detail: detail.into(),
    }
}

/// Move (or, cross-volume, copy) `from` into `to`. Only ever called from
/// `boot()`, before the sidecar is spawned — the one moment nothing has the
/// Oxigraph store open (ADR `app-data-on-disk.md` D4 follow-up). Returns
/// `None` when there was nothing to move (source missing/empty) — that is
/// not an error, just quietly nothing to report.
fn attempt_data_home_move(from: &Path, to: &Path) -> Option<StorageNotice> {
    let from_str = from.display().to_string();
    let to_str = to.display().to_string();

    // The destination must not be the source, nor sit inside it: rename() into
    // your own subtree is invalid, and the copy fallback would recurse forever
    // and fill the disk.
    let from_cmp = resolve_for_compare(from);
    let to_cmp = resolve_for_compare(to);
    if to_cmp == from_cmp || to_cmp.starts_with(&from_cmp) {
        return Some(move_failed(
            &from_str,
            &to_str,
            "移行先が移行元と同じか、その中にあります",
        ));
    }

    // Destination must be empty (or absent) before we touch anything.
    if to.exists() {
        if dir_has_entries(to) {
            return Some(move_failed(&from_str, &to_str, "移行先が空ではありません"));
        }
        // Empty already: drop it so rename()/copy below can (re)create it
        // cleanly — some platforms refuse to rename onto an existing dir.
        if let Err(err) = std::fs::remove_dir_all(to) {
            return Some(move_failed(
                &from_str,
                &to_str,
                format!("移行先を準備できません: {err}"),
            ));
        }
    }
    if let Some(parent) = to.parent() {
        if let Err(err) = std::fs::create_dir_all(parent) {
            return Some(move_failed(
                &from_str,
                &to_str,
                format!("移行先を作成できません: {err}"),
            ));
        }
    }

    // Nothing to move: quietly proceed with the (now-empty) new location.
    if !from.is_dir() || !dir_has_entries(from) {
        return None;
    }

    if std::fs::rename(from, to).is_ok() {
        return Some(StorageNotice {
            kind: "moved".into(),
            from: from_str,
            to: to_str,
            detail: "同じボリューム内だったため移動しました。".into(),
        });
    }

    // Cross-volume: rename() cannot do it atomically — copy instead, and
    // leave the source alone so nothing is lost even on partial failure.
    if let Err(err) = copy_dir_recursive(from, to) {
        let _ = std::fs::remove_dir_all(to);
        return Some(move_failed(
            &from_str,
            &to_str,
            format!("コピーに失敗しました: {err}"),
        ));
    }
    Some(StorageNotice {
        kind: "copied".into(),
        from: from_str,
        to: to_str,
        detail: "別ボリュームだったためコピーしました。元のフォルダはそのまま残っています。".into(),
    })
}

/// Runs at the very start of `boot()`, before the sidecar's `Command` is
/// built — the only safe moment to move the data home, since nothing has
/// the Oxigraph store open yet. Shows a splash window for the duration if
/// (and only if) a move was actually scheduled, since it can take tens of
/// seconds for a large store. No-op, silently, if nothing was scheduled.
fn perform_pending_move(app: &tauri::AppHandle) {
    let Some(from) = settings::take_pending_move_from(app) else {
        return;
    };
    let Some(to) = settings::read_data_home_override(app) else {
        // Nothing sensible to move into — treat as nothing scheduled.
        return;
    };

    // Say what is happening: copying hundreds of MB takes tens of seconds, and
    // a silent window reads as a hang. The splash is already on screen.
    let _ = ensure_splash(app);
    set_status(app, Status::working("moving"));

    if let Some(notice) = attempt_data_home_move(Path::new(&from), Path::new(&to)) {
        if notice.kind == "failed" {
            // Do not boot pointed at a destination that isn't actually
            // usable — fall back to wherever the backend's own default is.
            settings::clear_data_home_override(app);
        }
        settings::write_storage_notice(app, notice);
    }
}

// ---------------------------------------------------------------------------
// Menu
// ---------------------------------------------------------------------------

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

/// Grey out "バックエンドを再起動" until `boot()` has actually opened the MAIN
/// window (`Shell::booted`), and again while a retry re-enters `boot_once`.
/// The item is built disabled to begin with (see `build_menu`) so there is no
/// window at startup where it is clickable but `restart_backend` would still
/// refuse. Must NOT run on the main thread — same reason as
/// `set_update_menu_enabled`.
fn set_restart_menu_enabled(app: &tauri::AppHandle, enabled: bool) {
    let Some(menu) = app.menu() else { return };
    let Ok(items) = menu.items() else { return };
    let mut found = Vec::new();
    collect_menu_items(items, &["restart_backend"], &mut found);
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
    // Before anything else, including resolving the sidecar: this is the only
    // moment nothing has the Oxigraph store open (ADR `app-data-on-disk.md`
    // D9). Outside the retry loop — the move is consumed once, not per retry.
    perform_pending_move(&app);

    loop {
        match boot_once(&app) {
            Flow::Done => {
                spawn_watchdog(app.clone());
                return;
            }
            Flow::Retry => {
                // A retry after a timeout can leave a live child holding the
                // port: stop it (and take a fresh port) before going again.
                set_status(&app, Status::working("stopping"));
                stop_backend(&app);
                // Back to "not booted yet": another `boot_once` pass is about
                // to spawn/wait on `Shell.backend` again, so `restart_backend`
                // must refuse (and the menu item go grey) until it opens the
                // MAIN window again — see `Shell::booted`.
                app.state::<Shell>().booted.store(false, Ordering::SeqCst);
                set_restart_menu_enabled(&app, false);
            }
            Flow::Quit => {
                app.exit(1);
                return;
            }
        }
    }
}

/// Nothing polls the backend once the window is up — `wait_ready` only looks
/// during boot. A backend that then dies (observed once in production: an
/// external SIGTERM) is left `defunct` forever, and the SPA is stuck on
/// whatever screen it happened to be showing when the socket went away. This
/// thread's job is narrowly to reap that child so the process table stays
/// clean; it deliberately does NOT restart the backend on its own. Automatic
/// restart was considered and rejected (mirroring Graphium): a backend
/// crash-looping for a reason nobody can see would just crash-loop silently
/// instead of stopping where someone can notice, and would keep appending to
/// the log the whole time. `restart_backend` — reachable from the Help menu —
/// is the human-in-the-loop fix; this thread only keeps the plumbing clean
/// until someone uses it.
///
/// On an UNEXPECTED exit it also cleans up the process GROUP the dead
/// launcher left behind (the same production incident: Oxigraph / demo-agent
/// grandchildren survive their parent and go on holding the Oxigraph store
/// locked). This is also why `Shell::last_pgid` only needs to be kept around
/// for a couple of seconds at a time rather than for the life of the whole
/// app: the moment this loop notices the exit (within its 2s poll interval)
/// it `take()`s the pgid and signals it, so the window in which a pid the OS
/// could have already recycled for an unrelated process would be SIGKILLed
/// under a stale pgid stays this short — not "however long until someone
/// clicks restart".
fn spawn_watchdog(app: tauri::AppHandle) {
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_secs(2));
        let shell = app.state::<Shell>();
        if shell.shutting_down.load(Ordering::SeqCst) {
            return;
        }
        let exited = {
            let mut guard = shell.backend.lock().unwrap();
            let status = match guard.as_mut() {
                Some(child) => child.try_wait().ok().flatten(),
                None => None,
            };
            if status.is_some() {
                guard.take();
            }
            status
        };
        let Some(status) = exited else { continue };
        // Any of these three flags mean this exit was expected — the app is
        // stopping, updating (which restarts the whole process), or already
        // mid-`restart_backend` (which reaps the child itself, but that
        // reap and this one can race harmlessly: whichever gets there first
        // takes the `Option`, the other finds `None`). Each of those paths
        // does its own process-group cleanup, so this thread does not repeat
        // it for an expected exit — only for the exits nobody else is
        // watching for.
        let expected = shell.shutting_down.load(Ordering::SeqCst)
            || shell.updating.load(Ordering::SeqCst)
            || shell.restarting.load(Ordering::SeqCst);
        if expected {
            append_log(&app, &format!("backend exited (expected): {status}"));
            continue;
        }
        append_log(&app, &format!("backend exited: {status}"));
        #[cfg(unix)]
        {
            let pgid = shell.last_pgid.lock().unwrap().take();
            if let Some(pgid) = pgid {
                kill_process_group(pgid);
                append_log(
                    &app,
                    &format!("watchdog: cleaned up orphaned process group {pgid}"),
                );
            }
        }
    });
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
    // Recorded for `restart_backend`, which reuses this exact port rather
    // than resolving a fresh one (see `Shell::port`).
    *app.state::<Shell>().port.lock().unwrap() = Some(port);

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
    app.state::<Shell>()
        .ipc_granted
        .store(ipc_granted, Ordering::SeqCst);

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
/// Thin wrapper over `spawn_backend_quiet` that turns a failure into a stop
/// card — see that function for what actually happens.
fn spawn_backend(
    app: &tauri::AppHandle,
    backend: &BackendCmd,
    port: u16,
    ipc_granted: bool,
    log_path: Option<&Path>,
) -> Result<(), Flow> {
    spawn_backend_quiet(app, backend, port, ipc_granted, log_path).map_err(|err| {
        decide(
            app,
            Status::card("card.startFailed", err, &["retry", "log", "quit"]),
        )
    })
}

/// Build the `Command`, spawn it as a process-group leader, and record the
/// `Child` (and its pgid) in `Shell`. No splash/card side effects — used both
/// by `spawn_backend` (boot path, wraps failures in a stop card) and
/// `restart_backend` (background path, which has no card to show).
fn spawn_backend_quiet(
    app: &tauri::AppHandle,
    backend: &BackendCmd,
    port: u16,
    ipc_granted: bool,
    log_path: Option<&Path>,
) -> Result<(), String> {
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
    // Storage-location override (ADR `app-data-on-disk.md` D4): if the user
    // picked one in Settings, hand it to asterism-local the same way its own
    // CLI documents (`--data-dir`, same value as env ASTERISM_LOCAL_HOME).
    // Absent an override, behavior is unchanged — the backend's own default
    // applies. A path that cannot be created falls back to that default too,
    // rather than risk the app failing to boot over a bad setting.
    if let Some(dir) = settings::resolve_data_home_override(app) {
        command.args(["--data-dir", &dir]);
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
            let pgid = child.id() as i32;
            let state = app.state::<Shell>();
            *state.backend.lock().unwrap() = Some(child);
            *state.last_pgid.lock().unwrap() = Some(pgid);
            Ok(())
        }
        Err(err) => Err(format!("{err}\n{}", backend.program.display())),
    }
}

/// Poll until the backend answers on `port`, the tracked child exits, or
/// `timeout` elapses — no splash/card side effects (used both by `wait_ready`,
/// which wraps the outcome in a card, and `restart_backend`, which has no
/// card to show). The two failure shapes are told apart by the caller, not by
/// this function: after an `Err`, `Shell::backend` is `None` iff the child
/// exited (this loop reaps it, same as the boot path always has) — still
/// `Some` means the deadline simply ran out while the process was alive.
/// Why the backend is not answering. The two cases need opposite handling —
/// a dead child is a failure to report, a slow one is a wait to keep — and
/// callers used to tell them apart by peeking at `Shell.backend` (or, worse,
/// by the shape of an error string). Naming them removes that guesswork.
enum NotReady {
    /// The child exited. Carries the status plus the log tail that often says why.
    Exited(String),
    /// Still alive, just not answering yet. Carries what was waited for.
    Slow(String),
}

impl NotReady {
    fn detail(self) -> String {
        match self {
            NotReady::Exited(detail) | NotReady::Slow(detail) => detail,
        }
    }
}

/// Why a restart did not end with a live backend. `Slow` means a child IS
/// running and may still come up; every other case means nothing is coming.
/// The two get different wording, and the difference is carried in the type
/// rather than recovered from the shape of an error string.
enum RestartError {
    Slow(String),
    Failed(String),
}

impl RestartError {
    fn detail(&self) -> &str {
        match self {
            RestartError::Slow(detail) | RestartError::Failed(detail) => detail,
        }
    }
}

fn wait_ready_quiet(app: &tauri::AppHandle, port: u16, timeout: Duration) -> Result<(), NotReady> {
    let deadline = Instant::now() + timeout;
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
            return Err(NotReady::Exited(format!(
                "exit: {status}\n\n{}",
                log_tail(app, 50)
            )));
        }
        if Instant::now() > deadline {
            return Err(NotReady::Slow(format!(
                "no answer from 127.0.0.1:{port} after {}s",
                timeout.as_secs()
            )));
        }
        std::thread::sleep(Duration::from_millis(200));
    }
}

/// Wait for the backend to answer. Two things interrupt the wait: the child
/// dying (the log can often name why) and the wait growing long — which is a
/// question for the user, not a reason to quit on their behalf. A first launch
/// unpacks a ~370 MB bundle and gets scanned by Gatekeeper, so "slow" is not
/// the same as "broken".
fn wait_ready(app: &tauri::AppHandle, port: u16, bundled: bool) -> Result<(), Flow> {
    let mut timeout = READY_TIMEOUT;
    loop {
        let detail = match wait_ready_quiet(app, port, timeout) {
            Ok(()) => return Ok(()),
            Err(NotReady::Exited(detail)) => {
                let mut card =
                    Status::card("card.stopped", detail, &["retry", "log", "copy", "quit"])
                        .with_hint(plain_hint(&log_tail(app, 5)));
                if !bundled {
                    // A repo checkout is read by a developer: leave the technical
                    // text in front, where it is the fastest answer.
                    card = card.open_detail();
                }
                return Err(decide(app, card));
            }
            Err(NotReady::Slow(detail)) => detail,
        };
        let card = Status::card("card.slow", detail, &["wait", "log", "quit"]);
        match halt(app, card).as_str() {
            "wait" => {
                set_status(app, Status::working("starting"));
                timeout = READY_TIMEOUT;
            }
            "retry" => return Err(Flow::Retry),
            _ => return Err(Flow::Quit),
        }
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
            // The MAIN window is open and `Shell.backend` now holds the
            // backend that window is actually pointed at: `restart_backend`
            // may act on it from here on (see `Shell::booted`).
            app.state::<Shell>().booted.store(true, Ordering::SeqCst);
            set_restart_menu_enabled(app, true);
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
    // The last-resort recovery when the backend has died and the page cannot
    // help itself — see the death-watch thread in `boot()`. Built disabled:
    // `boot()` has not opened the MAIN window yet at this point, so there is
    // nothing for `restart_backend` to act on (see `Shell::booted`).
    // `open_main` flips it on once the window is actually up.
    let restart_backend_item =
        MenuItemBuilder::with_id("restart_backend", t("menu.restartBackend"))
            .enabled(false)
            .build(handle)?;
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
            .item(&restart_backend_item)
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
            .item(&restart_backend_item)
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
        .invoke_handler(tauri::generate_handler![
            boot_status,
            boot_action,
            get_data_home_override,
            set_data_home_override,
            get_storage_notice,
            clear_storage_notice,
            restart_backend
        ])
        .menu(build_menu)
        .on_menu_event(|app, event| {
            let id = event.id();
            if id == "check_update" || id == "check_update_help" {
                tauri::async_runtime::spawn(check_for_updates(app.clone()));
            } else if id == "open_log" {
                reveal_log(app);
            } else if id == "open_guide" {
                open_url(GUIDE_URL);
            } else if id == "restart_backend" {
                let handle = app.clone();
                tauri::async_runtime::spawn(async move {
                    match restart_backend_typed(handle.clone()).await {
                        Ok(_) => {
                            // The window is on an external origin
                            // (`http://127.0.0.1:<port>`), so there is no
                            // Tauri-level "reload" — ask the page itself to
                            // navigate again, now that a live backend is
                            // behind it.
                            if let Some(window) = handle.get_webview_window(MAIN) {
                                let _ = window.eval("location.reload()");
                            }
                            // No window open: nothing to reload, and opening
                            // one is out of scope for this command.
                        }
                        Err(err) => {
                            eprintln!("asterism-desktop: restart_backend failed: {}", err.detail());
                            // A child that is still starting is not a failure
                            // the user has to act on, so it gets its own,
                            // gentler sentence. The two cases are told apart
                            // by the type, not by the shape of the message.
                            let headline = match err {
                                RestartError::Slow(_) => t("menu.restartBackendSlow"),
                                RestartError::Failed(_) => t("menu.restartBackendFailed"),
                            };
                            // The plain sentence alone leaves the user with
                            // nothing to act on; the reason (port taken, no
                            // runtime, never became ready) is the part that
                            // decides what they do next, so it rides along.
                            notify(
                                &handle,
                                &format!("{headline}\n\n{}", err.detail()),
                                MessageDialogKind::Error,
                            );
                        }
                    }
                });
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

    #[test]
    fn restart_guard_refuses_a_second_call_while_the_first_holds_it() {
        let flag = AtomicBool::new(false);
        assert!(
            try_acquire_restart_guard(&flag),
            "first caller should acquire the guard"
        );
        assert!(
            !try_acquire_restart_guard(&flag),
            "a second caller must not acquire it while the first still holds it"
        );
        // Release (what the `ClearOnDrop` guard does in
        // `restart_backend_blocking`), then it is acquirable again.
        flag.store(false, Ordering::SeqCst);
        assert!(
            try_acquire_restart_guard(&flag),
            "the guard must be acquirable again once released"
        );
    }

    // `ui_lang()` latches its answer in a process-wide `OnceLock` on first
    // call, so a single test binary can only ever observe one of ja/en for
    // `t()` — there is no existing precedent in this file for forcing the
    // other branch mid-process. This asserts against whichever language the
    // test process resolves to (both arms of the match are non-empty
    // string literals either way, so this still catches an accidentally
    // empty/missing key).
    #[test]
    fn menu_restart_backend_label_is_never_empty() {
        assert!(!t("menu.restartBackend").is_empty());
        assert!(!t("menu.restartBackendFailed").is_empty());
        assert!(!t("menu.restartBackendSlow").is_empty());
    }

    #[test]
    fn restart_is_refused_until_boot_has_opened_the_main_window() {
        let booted = AtomicBool::new(false);
        assert!(
            !restart_allowed(&booted),
            "a restart mid-boot would steal the child boot_once is waiting on"
        );
        // What `open_main` does on `Flow::Done`.
        booted.store(true, Ordering::SeqCst);
        assert!(
            restart_allowed(&booted),
            "once the MAIN window is open, restart_backend has something to act on"
        );
        // What `boot()` does on `Flow::Retry` — back to "not booted yet"
        // because another `boot_once` pass is about to run.
        booted.store(false, Ordering::SeqCst);
        assert!(!restart_allowed(&booted));
    }

    // Tests for the boot-time data-home move (ADR `app-data-on-disk.md`
    // D9). This moves the user's actual data, so every failure path must be
    // proven to leave `from`/`to` untouched, not just the happy path.
    use std::fs;
    use tempfile::tempdir;

    fn write_file(path: &Path, contents: &str) {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(path, contents).unwrap();
    }

    /// A few levels of files/subdirectories, to prove recursive copy/move
    /// actually recurses rather than just handling a flat directory.
    fn populate_tree(root: &Path) {
        write_file(&root.join("top.txt"), "top");
        write_file(&root.join("a/nested.txt"), "nested");
        write_file(&root.join("a/b/deep.txt"), "deep");
    }

    fn assert_tree_present(root: &Path) {
        assert_eq!(fs::read_to_string(root.join("top.txt")).unwrap(), "top");
        assert_eq!(
            fs::read_to_string(root.join("a/nested.txt")).unwrap(),
            "nested"
        );
        assert_eq!(
            fs::read_to_string(root.join("a/b/deep.txt")).unwrap(),
            "deep"
        );
    }

    // ---- dir_has_entries ---------------------------------------------

    #[test]
    fn dir_has_entries_false_for_empty_dir() {
        let dir = tempdir().unwrap();
        assert!(!dir_has_entries(dir.path()));
    }

    #[test]
    fn dir_has_entries_false_for_os_cruft_only() {
        let dir = tempdir().unwrap();
        write_file(&dir.path().join(".DS_Store"), "");
        write_file(&dir.path().join(".localized"), "");
        assert!(!dir_has_entries(dir.path()));
    }

    #[test]
    fn dir_has_entries_true_for_real_file() {
        let dir = tempdir().unwrap();
        write_file(&dir.path().join(".DS_Store"), "");
        write_file(&dir.path().join("real.txt"), "hi");
        assert!(dir_has_entries(dir.path()));
    }

    #[test]
    fn dir_has_entries_false_for_unreadable_path() {
        let dir = tempdir().unwrap();
        let missing = dir.path().join("does-not-exist");
        assert!(!dir_has_entries(&missing));
    }

    // ---- resolve_for_compare -------------------------------------------

    #[test]
    fn resolve_for_compare_existing_path_is_canonicalized() {
        let dir = tempdir().unwrap();
        let resolved = resolve_for_compare(dir.path());
        assert_eq!(resolved, dir.path().canonicalize().unwrap());
    }

    #[test]
    fn resolve_for_compare_nonexistent_leaf_keeps_name_under_real_parent() {
        let dir = tempdir().unwrap();
        let target = dir.path().join("not-yet-created");
        let resolved = resolve_for_compare(&target);
        assert_eq!(
            resolved,
            dir.path().canonicalize().unwrap().join("not-yet-created")
        );
    }

    #[test]
    fn resolve_for_compare_nonexistent_middle_component_still_resolves() {
        let dir = tempdir().unwrap();
        let target = dir.path().join("also-missing/deeper/leaf");
        let resolved = resolve_for_compare(&target);
        assert_eq!(
            resolved,
            dir.path()
                .canonicalize()
                .unwrap()
                .join("also-missing/deeper/leaf")
        );
    }

    // ---- copy_dir_recursive / copy_symlink -----------------------------

    #[test]
    fn copy_dir_recursive_copies_nested_tree() {
        let dir = tempdir().unwrap();
        let from = dir.path().join("from");
        let to = dir.path().join("to");
        populate_tree(&from);

        copy_dir_recursive(&from, &to).unwrap();

        assert_tree_present(&to);
        // Source is untouched by the copy path itself.
        assert_tree_present(&from);
    }

    #[cfg(unix)]
    #[test]
    fn copy_dir_recursive_preserves_symlinks_as_symlinks() {
        let dir = tempdir().unwrap();
        let from = dir.path().join("from");
        let to = dir.path().join("to");
        fs::create_dir_all(&from).unwrap();
        write_file(&from.join("real.txt"), "hi");
        std::os::unix::fs::symlink("real.txt", from.join("link.txt")).unwrap();

        copy_dir_recursive(&from, &to).unwrap();

        let copied_link = to.join("link.txt");
        // symlink_metadata does not follow the link; if this were a regular
        // file (i.e. the link was dereferenced during copy), this would be
        // false.
        assert!(fs::symlink_metadata(&copied_link)
            .unwrap()
            .file_type()
            .is_symlink());
        assert_eq!(fs::read_link(&copied_link).unwrap(), Path::new("real.txt"));
    }

    // ---- attempt_data_home_move -----------------------------------------

    #[test]
    fn move_ordinary_case_moves_tree_and_removes_source() {
        let dir = tempdir().unwrap();
        let from = dir.path().join("from");
        let to = dir.path().join("to");
        populate_tree(&from);

        let notice = attempt_data_home_move(&from, &to).expect("expected a notice");

        assert_eq!(notice.kind, "moved");
        assert!(!from.exists());
        assert_tree_present(&to);
    }

    #[test]
    fn move_fails_when_destination_not_empty_and_leaves_everything_alone() {
        let dir = tempdir().unwrap();
        let from = dir.path().join("from");
        let to = dir.path().join("to");
        populate_tree(&from);
        write_file(&to.join("already-here.txt"), "existing");

        let notice = attempt_data_home_move(&from, &to).expect("expected a notice");

        assert_eq!(notice.kind, "failed");
        // Nothing was touched: source intact, destination's pre-existing
        // content intact, no partial writes into either.
        assert_tree_present(&from);
        assert_eq!(
            fs::read_to_string(to.join("already-here.txt")).unwrap(),
            "existing"
        );
        assert!(!to.join("top.txt").exists());
    }

    #[test]
    fn move_succeeds_when_destination_has_only_os_cruft() {
        let dir = tempdir().unwrap();
        let from = dir.path().join("from");
        let to = dir.path().join("to");
        populate_tree(&from);
        write_file(&to.join(".DS_Store"), "");
        write_file(&to.join(".localized"), "");

        let notice = attempt_data_home_move(&from, &to).expect("expected a notice");

        assert_eq!(notice.kind, "moved");
        assert!(!from.exists());
        assert_tree_present(&to);
    }

    #[test]
    fn move_fails_when_destination_equals_source() {
        let dir = tempdir().unwrap();
        let from = dir.path().join("from");
        populate_tree(&from);

        let notice = attempt_data_home_move(&from, &from).expect("expected a notice");

        assert_eq!(notice.kind, "failed");
        assert_tree_present(&from);
    }

    #[test]
    fn move_fails_when_destination_is_inside_source_existing_subdir() {
        let dir = tempdir().unwrap();
        let from = dir.path().join("from");
        populate_tree(&from);
        let to = from.join("sub");
        fs::create_dir_all(&to).unwrap();

        let notice = attempt_data_home_move(&from, &to).expect("expected a notice");

        assert_eq!(notice.kind, "failed");
        assert_tree_present(&from);
    }

    /// Same as above, but the destination path doesn't exist yet at any
    /// level (`from/sub/deeper`) — `resolve_for_compare` must still resolve
    /// it under `from` so this is rejected before any copying starts. A
    /// hang or runaway recursive copy would time out this test.
    #[test]
    fn move_fails_when_destination_is_inside_source_nonexistent_subdir() {
        let dir = tempdir().unwrap();
        let from = dir.path().join("from");
        populate_tree(&from);
        let to = from.join("sub/deeper");

        let notice = attempt_data_home_move(&from, &to).expect("expected a notice");

        assert_eq!(notice.kind, "failed");
        assert_tree_present(&from);
    }

    #[test]
    fn move_is_none_when_source_missing() {
        let dir = tempdir().unwrap();
        let from = dir.path().join("does-not-exist");
        let to = dir.path().join("to");

        assert!(attempt_data_home_move(&from, &to).is_none());
    }

    #[test]
    fn move_is_none_when_source_is_empty_dir() {
        let dir = tempdir().unwrap();
        let from = dir.path().join("from");
        fs::create_dir_all(&from).unwrap();
        let to = dir.path().join("to");

        assert!(attempt_data_home_move(&from, &to).is_none());
    }

    /// `attempt_data_home_move`'s copy-fallback cleanup
    /// (`std::fs::remove_dir_all(to)` after a failed `copy_dir_recursive`)
    /// can't be reached from the same-volume rename path this test suite
    /// runs under (temp dirs are on one volume, so `rename` always wins
    /// first). Instead this exercises the same cleanup pattern directly:
    /// force `copy_dir_recursive` to fail partway through (an unreadable
    /// source file after the destination has already received one entry),
    /// then confirm that following the real code's cleanup step removes
    /// the partial destination rather than leaving debris behind.
    #[cfg(unix)]
    #[test]
    fn copy_dir_recursive_failure_leaves_no_partial_destination_after_cleanup() {
        use std::os::unix::fs::PermissionsExt;

        let dir = tempdir().unwrap();
        let from = dir.path().join("from");
        let to = dir.path().join("to");
        write_file(&from.join("ok.txt"), "ok");
        write_file(&from.join("bad.txt"), "bad");
        // Make one file unreadable so the recursive copy fails partway
        // through, after `to` has already been created and at least one
        // entry copied.
        fs::set_permissions(from.join("bad.txt"), fs::Permissions::from_mode(0o000)).unwrap();

        let result = copy_dir_recursive(&from, &to);
        // Restore permissions so tempdir cleanup can remove the file.
        fs::set_permissions(from.join("bad.txt"), fs::Permissions::from_mode(0o644)).unwrap();

        assert!(result.is_err(), "copy should fail on the unreadable file");
        assert!(to.exists(), "partial destination exists before cleanup");

        // This mirrors exactly what attempt_data_home_move does on a copy
        // failure.
        let _ = fs::remove_dir_all(&to);
        assert!(!to.exists(), "cleanup must remove the partial destination");
    }
}
