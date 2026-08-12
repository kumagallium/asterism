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

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

const READY_TIMEOUT: Duration = Duration::from_secs(60);

struct Backend(Mutex<Option<Child>>);

fn free_port() -> std::io::Result<u16> {
    Ok(TcpListener::bind("127.0.0.1:0")?.local_addr()?.port())
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

fn boot(app: tauri::AppHandle) {
    let Some(launcher) = find_launcher() else {
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

    let mut command = Command::new(&launcher);
    command
        .args(["--no-browser", "--port", &port.to_string()])
        .stdin(Stdio::null())
        .stdout(stdout)
        .stderr(stderr);
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
                    launcher.display()
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
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(Backend(Mutex::new(None)))
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
