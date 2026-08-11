use std::fs;
use std::io::ErrorKind;
use std::path::PathBuf;

use tauri::{AppHandle, Manager};

const ONBOARDING_STATE_FILE: &str = "desktop-onboarding-v1";
const PENDING: &str = "pending";
const COMPLETE: &str = "complete";

fn state_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_local_data_dir()
        .map(|directory| directory.join(ONBOARDING_STATE_FILE))
        .map_err(|error| format!("无法解析桌面引导状态目录: {error}"))
}

fn normalize_status(raw: &str) -> Option<&'static str> {
    match raw.trim() {
        PENDING => Some(PENDING),
        COMPLETE => Some(COMPLETE),
        _ => None,
    }
}

#[tauri::command]
pub fn get_desktop_onboarding_status(app: AppHandle) -> Result<Option<String>, String> {
    let path = state_path(&app)?;
    match fs::read_to_string(&path) {
        Ok(raw) => Ok(normalize_status(&raw).map(str::to_string)),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(error) => Err(format!("无法读取桌面引导状态: {error}")),
    }
}

#[tauri::command]
pub fn set_desktop_onboarding_status(app: AppHandle, status: String) -> Result<(), String> {
    let status = normalize_status(&status).ok_or_else(|| "无效的桌面引导状态".to_string())?;
    let path = state_path(&app)?;
    let directory = path
        .parent()
        .ok_or_else(|| "无法解析桌面引导状态目录".to_string())?;
    fs::create_dir_all(directory).map_err(|error| format!("无法创建桌面引导状态目录: {error}"))?;
    fs::write(&path, status).map_err(|error| format!("无法保存桌面引导状态: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_only_supported_onboarding_states() {
        assert_eq!(normalize_status("pending\n"), Some(PENDING));
        assert_eq!(normalize_status("complete"), Some(COMPLETE));
        assert_eq!(normalize_status("unknown"), None);
    }
}
