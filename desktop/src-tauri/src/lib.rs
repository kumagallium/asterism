//! Asterism desktop shell — Phase 2 v1 of ADR `local-first-distribution.md`.
//!
//! The shell owns exactly one contract: spawn the `asterism-local` launcher
//! (which itself supervises Oxigraph and the demo-agent as children) as a
//! process-group leader, wait for HTTP readiness on a free loopback port, then
//! open the native window at that URL. On quit, signal the whole group
//! (SIGTERM, then SIGKILL) so no grandchild can be orphaned.
//!
//! v1 resolves the launcher from a repo checkout (`api/.venv/bin/asterism-local`
//! found by walking up from the executable, or `ASTERISM_LOCAL_CMD`, or PATH).
//! Bundling a self-contained Python runtime is the follow-up step; the process
//! contract stays the same, mirroring Graphium's sidecar layout.
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
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::ipc::CapabilityBuilder;
use tauri::menu::{MenuBuilder, MenuItemBuilder, SubmenuBuilder};
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

const READY_TIMEOUT: Duration = Duration::from_secs(60);

struct Backend(Mutex<Option<Child>>);

fn free_port() -> std::io::Result<u16> {
    Ok(TcpListener::bind("127.0.0.1:0")?.local_addr()?.port())
}

// The window loads http://127.0.0.1:<port>/. The browser keys localStorage
// (registered models, default model, remembered API keys — ui/src/settings)
// by ORIGIN, so a per-launch random port would silently wipe every setting on
// each restart. Pin a fixed loopback port so the origin — and therefore the
// stored settings — is stable across launches (the same reason Graphium pins
// 127.0.0.1:3001). Fall back to a random port only if it is already taken, in
// which case settings are ephemeral for that session but the app still runs.
const PREFERRED_PORT: u16 = 8765;

fn app_port() -> u16 {
    match TcpListener::bind(("127.0.0.1", PREFERRED_PORT)) {
        Ok(listener) => {
            drop(listener);
            PREFERRED_PORT
        }
        Err(_) => free_port().unwrap_or(PREFERRED_PORT),
    }
}

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

/// Minimal HTTP readiness probe. Any HTTP status line counts — `/health` may
/// legitimately be 503 while the store warms up, but the SPA is served as soon
/// as uvicorn answers.
fn http_ready(port: u16) -> bool {
    let addr = match format!("127.0.0.1:{port}").parse() {
        Ok(a) => a,
        Err(_) => return false,
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(500)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(700)));
    let request =
        format!("GET /health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0u8; 12];
    matches!(stream.read(&mut buf), Ok(n) if n >= 8 && buf.starts_with(b"HTTP/1."))
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

fn fail(app: &tauri::AppHandle, message: &str) {
    app.dialog()
        .message(message)
        .kind(MessageDialogKind::Error)
        .title("Asterism")
        .blocking_show();
    app.exit(1);
}

/// Non-fatal notice (unlike `fail`, which exits).
fn notify(app: &tauri::AppHandle, message: &str, kind: MessageDialogKind) {
    app.dialog()
        .message(message)
        .kind(kind)
        .title("Asterism のアップデート")
        .blocking_show();
}

/// Native update flow behind the menu item "アップデートを確認…" — the fallback
/// that needs no page: checks the release `latest.json` against the bundled
/// version, offers a native dialog, then downloads, installs, and relaunches.
/// The everyday surface is the SPA banner (see the module doc); this stays for
/// when the page cannot help (blank window, IPC refused, a broken build).
/// Always user-initiated, so every outcome — including "already up to date"
/// and errors — gets a dialog.
async fn check_for_updates(app: tauri::AppHandle) {
    let updater = match app.updater() {
        Ok(updater) => updater,
        Err(err) => {
            notify(
                &app,
                &format!("アップデータを初期化できません: {err}"),
                MessageDialogKind::Error,
            );
            return;
        }
    };
    match updater.check().await {
        Ok(Some(update)) => {
            let message = format!(
                "新しいバージョン {} が利用できます（現在 {}）。\n今すぐダウンロードして更新しますか？\n（更新後にアプリを再起動します）",
                update.version, update.current_version
            );
            let accepted = app
                .dialog()
                .message(message)
                .title("Asterism のアップデート")
                .buttons(MessageDialogButtons::OkCancelCustom(
                    "今すぐ更新".to_string(),
                    "後で".to_string(),
                ))
                .blocking_show();
            if !accepted {
                return;
            }
            if let Err(err) = update
                .download_and_install(|_downloaded, _total| {}, || {})
                .await
            {
                notify(
                    &app,
                    &format!("更新のダウンロード/インストールに失敗しました: {err}"),
                    MessageDialogKind::Error,
                );
                return;
            }
            app.restart();
        }
        Ok(None) => notify(
            &app,
            "お使いの Asterism は最新です。",
            MessageDialogKind::Info,
        ),
        Err(err) => notify(
            &app,
            &format!("アップデートの確認に失敗しました: {err}"),
            MessageDialogKind::Error,
        ),
    }
}

/// Let the SPA drive updates — and the storage-location setting — from
/// inside the window. The page is a remote `http://127.0.0.1:<port>` origin,
/// and Tauri grants remote origins nothing unless a capability names them
/// (the static `capabilities/default.json` covers local `tauri://` pages
/// only). This registers, at runtime, a capability scoped to the ONE origin
/// the window is about to load — port included, so it also holds on the
/// random-port fallback — that opens exactly:
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
/// - `dialog:allow-open`: lets the storage-location setting show a native
///   folder picker. Only `open` — `save`/`message`/etc. stay closed (#377's
///   "grant only the IPC that is needed" policy).
///
/// Nothing else — no shell, fs, or window control reaches the page.
fn grant_spa_update_ipc(app: &tauri::AppHandle, port: u16) -> tauri::Result<()> {
    let capability = CapabilityBuilder::new("loopback-spa-updater")
        .local(false)
        .remote(format!("http://127.0.0.1:{port}"))
        .window("main")
        .permission("updater:default")
        .permission("process:allow-restart")
        .permission("core:resources:allow-close")
        .permission("allow-get-data-home-override")
        .permission("allow-set-data-home-override")
        .permission("dialog:allow-open");
    app.add_capability(capability)
}

/// Current storage-location override, if any (ADR `app-data-on-disk.md` D4).
/// `None` means "use the backend's own default data dir".
#[tauri::command]
fn get_data_home_override(app: tauri::AppHandle) -> Option<String> {
    settings::read_data_home_override(&app)
}

/// Save (or, with `path: None`, clear) the storage-location override.
/// Save-only: does not restart the backend. The new location is picked up on
/// the next launch (same as Graphium).
#[tauri::command]
fn set_data_home_override(app: tauri::AppHandle, path: Option<String>) {
    settings::write_data_home_override(&app, path);
}

fn boot(app: tauri::AppHandle) {
    let Some(backend) = bundled_backend(&app).or_else(checkout_backend) else {
        fail(
            &app,
            "asterism-local が見つかりません。\n\nリポジトリで一度だけ準備してください:\n  cd api && uv venv .venv \\\n    && uv pip install -e ../ingest && uv pip install -e '.[local]'\n\n（場所を指定する場合は環境変数 ASTERISM_LOCAL_CMD）",
        );
        return;
    };
    let port = app_port();
    // Before the window exists: the grant is keyed by the origin the window
    // will load. Failure is not fatal — the menu item still updates the app.
    if let Err(err) = grant_spa_update_ipc(&app, port) {
        eprintln!("asterism-desktop: could not grant updater IPC to the SPA: {err}");
    }

    let log_path = app.path().app_log_dir().ok().map(|dir| {
        let _ = std::fs::create_dir_all(&dir);
        dir.join("backend.log")
    });
    let log_hint = log_path
        .as_deref()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|| "(ログなし)".to_string());
    let (stdout, stderr) = match &log_path {
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
    if let Some(dir) = settings::resolve_data_home_override(&app) {
        command.args(["--data-dir", &dir]);
    }
    // Tell the backend which build it belongs to: `/api/instance` relays it, so
    // the SPA shows the version (settings → About) and knows it is running
    // inside the desktop app — without any IPC beyond the updater grant above.
    command.env(
        "ASTERISM_APP_VERSION",
        app.package_info().version.to_string(),
    );
    // New process group with pgid = launcher pid: the Oxigraph / demo-agent
    // grandchildren inherit it, so terminate() can signal the whole tree.
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    let spawned = command.spawn();
    let child = match spawned {
        Ok(c) => c,
        Err(e) => {
            fail(
                &app,
                &format!(
                    "asterism-local の起動に失敗しました: {e}\n{}",
                    backend.program.display()
                ),
            );
            return;
        }
    };
    *app.state::<Backend>().0.lock().unwrap() = Some(child);

    let deadline = Instant::now() + READY_TIMEOUT;
    loop {
        if http_ready(port) {
            break;
        }
        {
            let state = app.state::<Backend>();
            let mut guard = state.0.lock().unwrap();
            if let Some(child) = guard.as_mut() {
                if let Ok(Some(status)) = child.try_wait() {
                    guard.take();
                    fail(
                        &app,
                        &format!(
                            "バックエンドが終了しました ({status})。\nOxigraph が未インストールの可能性があります（brew install oxigraph）。\nログ: {log_hint}"
                        ),
                    );
                    return;
                }
            }
        }
        if Instant::now() > deadline {
            fail(
                &app,
                &format!(
                    "バックエンドが {}s 以内に応答しませんでした。\nログ: {log_hint}",
                    READY_TIMEOUT.as_secs()
                ),
            );
            return;
        }
        std::thread::sleep(Duration::from_millis(200));
    }

    let url = format!("http://127.0.0.1:{port}/");
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        let built = WebviewWindowBuilder::new(
            &handle,
            "main",
            WebviewUrl::External(url.parse().expect("loopback url")),
        )
        .title("Asterism")
        .inner_size(1280.0, 800.0)
        .min_inner_size(900.0, 600.0)
        .center()
        .build();
        if built.is_err() {
            handle.exit(1);
        }
    });
    // No native auto-check here any more: the SPA checks once the window is up
    // and shows a banner instead of a modal dialog (ui/src/desktop/updater.ts).
}

/// macOS-standard menu (so webview copy/paste keeps working) with a
/// "Check for Updates" item in the app submenu; a minimal Help menu elsewhere.
fn build_menu<R: tauri::Runtime>(
    handle: &tauri::AppHandle<R>,
) -> tauri::Result<tauri::menu::Menu<R>> {
    let check = MenuItemBuilder::with_id("check_update", "アップデートを確認…").build(handle)?;
    #[cfg(target_os = "macos")]
    {
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
        let edit_menu = SubmenuBuilder::new(handle, "Edit")
            .undo()
            .redo()
            .separator()
            .cut()
            .copy()
            .paste()
            .select_all()
            .build()?;
        let window_menu = SubmenuBuilder::new(handle, "Window")
            .minimize()
            .separator()
            .close_window()
            .build()?;
        MenuBuilder::new(handle)
            .items(&[&app_menu, &edit_menu, &window_menu])
            .build()
    }
    #[cfg(not(target_os = "macos"))]
    {
        let help_menu = SubmenuBuilder::new(handle, "Help").item(&check).build()?;
        MenuBuilder::new(handle).items(&[&help_menu]).build()
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .manage(Backend(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![
            get_data_home_override,
            set_data_home_override
        ])
        .menu(build_menu)
        .on_menu_event(|app, event| {
            if event.id() == "check_update" {
                tauri::async_runtime::spawn(check_for_updates(app.clone()));
            }
        })
        .setup(|app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || boot(handle));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(mut child) = app.state::<Backend>().0.lock().unwrap().take() {
                    terminate(&mut child);
                }
            }
        });
}
