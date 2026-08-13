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

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::menu::{MenuBuilder, MenuItemBuilder, SubmenuBuilder};
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

const READY_TIMEOUT: Duration = Duration::from_secs(60);
// The updater plugin can't install over a dev build, and the auto-check would
// annoy on every `tauri dev` run; the menu item still works in all builds.
const AUTO_CHECK_DELAY: Duration = Duration::from_secs(8);

struct Backend(Mutex<Option<Child>>);

fn free_port() -> std::io::Result<u16> {
    Ok(TcpListener::bind("127.0.0.1:0")?.local_addr()?.port())
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

/// Native auto-update flow (chosen surface: no SPA/IPC coupling, since the
/// window is a remote http://127.0.0.1 URL). Checks the release `latest.json`
/// against the bundled version, offers a native dialog, then downloads,
/// installs, and relaunches. `user_initiated` controls whether "already up to
/// date" and errors surface a dialog (menu check) or stay silent (auto-check).
async fn check_for_updates(app: tauri::AppHandle, user_initiated: bool) {
    let updater = match app.updater() {
        Ok(updater) => updater,
        Err(err) => {
            if user_initiated {
                notify(
                    &app,
                    &format!("アップデータを初期化できません: {err}"),
                    MessageDialogKind::Error,
                );
            }
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
        Ok(None) => {
            if user_initiated {
                notify(
                    &app,
                    "お使いの Asterism は最新です。",
                    MessageDialogKind::Info,
                );
            }
        }
        Err(err) => {
            if user_initiated {
                notify(
                    &app,
                    &format!("アップデートの確認に失敗しました: {err}"),
                    MessageDialogKind::Error,
                );
            }
        }
    }
}

fn boot(app: tauri::AppHandle) {
    let Some(backend) = bundled_backend(&app).or_else(checkout_backend) else {
        fail(
            &app,
            "asterism-local が見つかりません。\n\nリポジトリで一度だけ準備してください:\n  cd api && uv venv .venv \\\n    && uv pip install -e ../ingest && uv pip install -e '.[local]'\n\n（場所を指定する場合は環境変数 ASTERISM_LOCAL_CMD）",
        );
        return;
    };
    let port = match free_port() {
        Ok(p) => p,
        Err(e) => {
            fail(&app, &format!("空きポートの確保に失敗しました: {e}"));
            return;
        }
    };

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

    // Auto-check for updates once the window is up (release builds only).
    #[cfg(not(debug_assertions))]
    {
        let handle = app.clone();
        std::thread::spawn(move || {
            std::thread::sleep(AUTO_CHECK_DELAY);
            tauri::async_runtime::spawn(check_for_updates(handle, false));
        });
    }
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
        .menu(build_menu)
        .on_menu_event(|app, event| {
            if event.id() == "check_update" {
                tauri::async_runtime::spawn(check_for_updates(app.clone(), true));
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
