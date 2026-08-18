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

#[derive(Default, Serialize, Deserialize)]
struct StoredSettings {
    /// Overrides where `asterism-local` keeps its data (datasets, graphs,
    /// chat threads, settings). `None` = use the backend's own default.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    data_home_override: Option<String>,
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

/// Persist (or, with `None`, clear) the storage-location override. Save-only:
/// does not touch the running backend. Failure is logged and swallowed —
/// never worth crashing the app over a settings write.
pub fn write_data_home_override(app: &tauri::AppHandle, path: Option<String>) {
    let Some(settings_file) = settings_path(app) else {
        return;
    };
    let mut settings = load(app);
    settings.data_home_override = path;
    match serde_json::to_string_pretty(&settings) {
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
