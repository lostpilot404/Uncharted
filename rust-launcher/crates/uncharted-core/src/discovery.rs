//! Game-folder detection and validation, mirroring `game_info()` in the Python launcher:
//! a folder is a game folder when it holds one of the collection's executables; the shown
//! title/id/version come from sce_sys/param.sfo.

use crate::settings::{Settings, GAMES};
use crate::sfo::{parse_sfo, SfoValue};
use std::path::Path;

#[derive(Debug, Clone, PartialEq)]
pub struct GameInfo {
    pub title: String,
    pub id: String,
    pub version: String,
    /// Which of u1/u2/u3 are present in this folder.
    pub games: Vec<String>,
}

impl GameInfo {
    /// None when the folder holds none of the collection's executables.
    pub fn inspect(folder: &Path) -> Option<GameInfo> {
        let mut games = Vec::new();
        for (key, _title, exe) in GAMES {
            if folder.join(exe).is_file() {
                games.push(key.to_string());
            }
        }
        if games.is_empty() {
            return None;
        }
        let mut info = GameInfo {
            title: "Uncharted".into(),
            id: folder
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default(),
            version: "?".into(),
            games,
        };
        if let Ok(bytes) = std::fs::read(folder.join("sce_sys/param.sfo")) {
            if let Ok(values) = parse_sfo(&bytes) {
                if let Some(SfoValue::Str(title)) = values.get("TITLE") {
                    info.title = title.replace('™', "").trim().to_string();
                }
                if let Some(SfoValue::Str(id)) = values.get("TITLE_ID") {
                    info.id = id.clone();
                }
                if let Some(SfoValue::Str(version)) = values.get("APP_VER") {
                    info.version = version.clone();
                }
            }
        }
        Some(info)
    }
}

/// Where each game would be started from and whether its executable exists there.
#[derive(Debug, Clone, PartialEq)]
pub struct GameStatus {
    pub key: String,
    pub title: String,
    pub folder: Option<String>,
    pub executable_present: bool,
}

pub fn game_statuses(settings: &Settings) -> Vec<GameStatus> {
    GAMES
        .iter()
        .map(|(key, title, exe)| {
            let folder = settings.game_dir_for(key);
            let folder = if folder.trim().is_empty() {
                None
            } else {
                Some(folder.to_string())
            };
            let executable_present = folder
                .as_deref()
                .map(|f| Path::new(f).join(exe).is_file())
                .unwrap_or(false);
            GameStatus {
                key: (*key).into(),
                title: (*title).into(),
                folder,
                executable_present,
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn folder_without_executables_is_not_a_game_folder() {
        let dir = std::env::temp_dir().join("uncharted-core-test-empty");
        std::fs::create_dir_all(&dir).unwrap();
        assert_eq!(GameInfo::inspect(&dir), None);
    }

    #[test]
    fn executable_presence_drives_detection() {
        let dir = std::env::temp_dir().join("uncharted-core-test-game");
        std::fs::create_dir_all(dir.join("sce_sys")).unwrap();
        std::fs::write(dir.join("eboot.bin"), b"stub").unwrap();
        // param.sfo is optional: the folder name is the fallback id.
        let info = GameInfo::inspect(&dir).expect("detected");
        assert_eq!(info.games, vec!["u1".to_string()]);
        assert_eq!(info.version, "?");
        std::fs::write(dir.join("big3-ps4_Shipping.elf"), b"stub").unwrap();
        let info = GameInfo::inspect(&dir).expect("detected");
        assert_eq!(info.games, vec!["u1".to_string(), "u3".to_string()]);
    }

    #[test]
    fn statuses_use_per_game_folders() {
        let mut s = Settings::default();
        s.game_dir = "X:/collection".into();
        s.game_dir_u2 = "X:/u2".into();
        let status = game_statuses(&s);
        assert_eq!(status[0].folder.as_deref(), Some("X:/collection"));
        assert_eq!(status[1].folder.as_deref(), Some("X:/u2"));
        assert!(!status[0].executable_present);
    }
}
