//! Small on-disk settings file for the shell itself (NOT app data — app data
//! lives under the `asterism-local` data home, which is exactly what this
//! file lets the user redirect; see ADR `app-data-on-disk.md` D4).
//!
//! Lives in the Tauri app config dir (macOS:
//! `~/Library/Application Support/com.kumagallium.asterism/settings.json`),
//! separate from the data home on purpose — the data home itself can move,
//! so the pointer to it cannot live inside it.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use tauri::Manager;

/// Result of a boot-time data-home move attempt (ADR `app-data-on-disk.md`
/// D4 follow-up: "helping the user move" without touching a live store).
/// Reported to the SPA once via `get_storage_notice`, then cleared.
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StorageNotice {
    /// "moved" | "copied" | "failed".
    pub kind: String,
    pub from: String,
    pub to: String,
    pub detail: String,
}

#[derive(Default, Serialize, Deserialize)]
struct StoredSettings {
    /// Overrides where `asterism-local` keeps its data (datasets, graphs,
    /// chat threads, settings). `None` = use the backend's own default.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    data_home_override: Option<String>,
    /// Set together with `data_home_override` when the user asked to move
    /// the existing data along: the absolute path the *next* boot should
    /// move (or copy) from, before starting the sidecar. Cleared after that
    /// boot runs, regardless of outcome.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pending_move_from: Option<String>,
    /// Outcome of the most recent move attempt, for the SPA to show once.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    storage_notice: Option<StorageNotice>,
}

fn settings_path(app: &tauri::AppHandle) -> Option<PathBuf> {
    let dir = match app.path().app_config_dir() {
        Ok(dir) => dir,
        Err(err) => {
            eprintln!("asterism-desktop: could not resolve app config dir: {err}");
            return None;
        }
    };
    if let Err(err) = std::fs::create_dir_all(&dir) {
        eprintln!(
            "asterism-desktop: could not create app config dir {}: {err}",
            dir.display()
        );
        return None;
    }
    Some(dir.join("settings.json"))
}

fn load(app: &tauri::AppHandle) -> StoredSettings {
    let Some(path) = settings_path(app) else {
        return StoredSettings::default();
    };
    match std::fs::read_to_string(&path) {
        Ok(text) => serde_json::from_str(&text).unwrap_or_default(),
        Err(_) => StoredSettings::default(),
    }
}

/// Current storage-location override, if the user set one. Never fails
/// outward — any read/parse problem is treated as "unset" (falls back to the
/// backend's own default data dir).
pub fn read_data_home_override(app: &tauri::AppHandle) -> Option<String> {
    load(app).data_home_override
}

/// Storage-location override validated for boot: the saved path, if it
/// exists (creating it if needed). If the path cannot be created, the
/// override is ignored — the caller falls back to the backend's own
/// default — and the reason is logged rather than failing the launch.
pub fn resolve_data_home_override(app: &tauri::AppHandle) -> Option<String> {
    let raw = read_data_home_override(app)?;
    if raw.trim().is_empty() {
        return None;
    }
    let path = PathBuf::from(&raw);
    if let Err(err) = std::fs::create_dir_all(&path) {
        eprintln!(
            "asterism-desktop: storage-location override {} is unusable ({err}); \
             falling back to the default data dir",
            path.display()
        );
        return None;
    }
    Some(raw)
}

fn save(app: &tauri::AppHandle, settings: &StoredSettings) {
    let Some(settings_file) = settings_path(app) else {
        return;
    };
    match serde_json::to_string_pretty(settings) {
        Ok(json) => {
            if let Err(err) = std::fs::write(&settings_file, json) {
                eprintln!(
                    "asterism-desktop: could not write settings {}: {err}",
                    settings_file.display()
                );
            }
        }
        Err(err) => eprintln!("asterism-desktop: could not serialize settings: {err}"),
    }
}

/// Persist (or, with `path: None`, clear) the storage-location override.
/// Save-only: does not touch the running backend or the filesystem beyond
/// this settings file. Failure is logged and swallowed — never worth
/// crashing the app over a settings write.
///
/// `move_from`, when `Some`, additionally schedules "move the existing data
/// from this absolute path to `path` on the next launch, before the sidecar
/// starts" (see `take_pending_move` and `lib.rs::boot`). It only makes sense
/// paired with `Some(path)`; passing it alongside `path: None` schedules
/// nothing (there is no destination to move to).
pub fn write_data_home_override(
    app: &tauri::AppHandle,
    path: Option<String>,
    move_from: Option<String>,
) {
    let mut settings = load(app);
    if path.is_some() {
        settings.pending_move_from = move_from;
    }
    settings.data_home_override = path;
    save(app, &settings);
}

/// Take (read and clear) the scheduled move source, if any. Called once at
/// boot, before the sidecar is spawned — see `lib.rs::boot`.
pub fn take_pending_move_from(app: &tauri::AppHandle) -> Option<String> {
    let mut settings = load(app);
    let from = settings.pending_move_from.take();
    if from.is_some() {
        save(app, &settings);
    }
    from
}

/// Record the outcome of a boot-time move attempt, for the SPA to read once
/// via `get_storage_notice`.
pub fn write_storage_notice(app: &tauri::AppHandle, notice: StorageNotice) {
    let mut settings = load(app);
    settings.storage_notice = Some(notice);
    save(app, &settings);
}

/// Take (read and clear) the stored storage-location override, restoring it
/// to `None` — used when a move fails and boot must fall back to the old
/// location instead of the (unusable) new one the user picked.
pub fn clear_data_home_override(app: &tauri::AppHandle) {
    let mut settings = load(app);
    settings.data_home_override = None;
    save(app, &settings);
}

/// Read the last move outcome without clearing it (`get_storage_notice`
/// IPC command).
pub fn read_storage_notice(app: &tauri::AppHandle) -> Option<StorageNotice> {
    load(app).storage_notice
}

/// Clear the last move outcome once the SPA has shown it
/// (`clear_storage_notice` IPC command).
pub fn clear_storage_notice(app: &tauri::AppHandle) {
    let mut settings = load(app);
    if settings.storage_notice.take().is_some() {
        save(app, &settings);
    }
}
