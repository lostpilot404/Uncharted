//! Launcher settings: the on-disk format is exactly `%APPDATA%/uncharted-launcher/settings.json`
//! as written by `launcher/uncharted_launcher.py` (`APP_DEFAULTS`), so the Rust and Python
//! launchers can read each other's files. Unknown keys are preserved across a load/save round
//! trip (settings from a newer launcher must not be silently dropped).

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// The three games of the collection and the executable each needs in its folder.
/// Mirrors `GAMES` in launcher/uncharted_launcher.py.
pub const GAMES: [(&str, &str, &str); 3] = [
    ("u1", "Drake's Fortune", "eboot.bin"),
    ("u2", "Among Thieves", "big2-ps4_Shipping.elf"),
    ("u3", "Drake's Deception", "big3-ps4_Shipping.elf"),
];

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case", default)]
pub struct Settings {
    pub game_dir: String,
    pub game_dir_u1: String,
    pub game_dir_u2: String,
    pub game_dir_u3: String,
    pub user_dir: String,
    /// Which game the Play button starts: "u1" | "u2" | "u3".
    pub start: String,
    /// PS4 system language number as a decimal string ("1" = English US).
    pub language: String,
    pub player_name: String,
    pub fullscreen: bool,
    pub present_mode: String,
    pub frame_rate: String,
    pub show_fps: bool,
    pub window_size: String,
    pub scaler: String,
    pub sharpening: String,
    pub anti_aliasing: String,
    pub aa_sharpness: String,
    pub frame_generation: String,
    pub frame_gen_multiplier: String,
    pub extra_env: String,
    pub close_on_play: bool,
    pub mouse_look: bool,
    pub mouse_sensitivity: String,
    pub mouse_invert_y: bool,
    pub pad_invert_y: bool,
    #[serde(default)]
    pub key_bindings: BTreeMap<String, String>,
    #[serde(default)]
    pub pad_bindings: BTreeMap<String, String>,
    pub check_updates: bool,
    /// Keys this build does not know (written by newer versions): preserved on save.
    #[serde(flatten)]
    pub unknown: BTreeMap<String, serde_json::Value>,
}

impl Default for Settings {
    fn default() -> Self {
        // APP_DEFAULTS in launcher/uncharted_launcher.py.
        Self {
            game_dir: String::new(),
            game_dir_u1: String::new(),
            game_dir_u2: String::new(),
            game_dir_u3: String::new(),
            user_dir: String::new(),
            start: "u1".into(),
            language: "1".into(),
            player_name: String::new(),
            fullscreen: false,
            present_mode: "Mailbox".into(),
            frame_rate: "60".into(),
            show_fps: false,
            window_size: "1920x1080".into(),
            scaler: "fsr1".into(),
            sharpening: "500".into(),
            anti_aliasing: "off".into(),
            aa_sharpness: "0.2".into(),
            frame_generation: "off".into(),
            frame_gen_multiplier: "2".into(),
            extra_env: String::new(),
            close_on_play: false,
            mouse_look: true,
            mouse_sensitivity: "1.0".into(),
            mouse_invert_y: false,
            pad_invert_y: false,
            key_bindings: BTreeMap::new(),
            pad_bindings: BTreeMap::new(),
            check_updates: true,
            unknown: BTreeMap::new(),
        }
    }
}

impl Settings {
    /// Parse settings.json. An empty or unreadable file yields defaults (same as the
    /// Python launcher's `load_json(CONFIG_FILE, {})` merge over APP_DEFAULTS).
    /// The Python launcher reads with utf-8-sig: a UTF-8 BOM must not fail the parse.
    pub fn from_json(text: &str) -> Result<Self, String> {
        let text = text.strip_prefix('\u{feff}').unwrap_or(text);
        if text.trim().is_empty() {
            return Ok(Self::default());
        }
        serde_json::from_str(text).map_err(|e| format!("settings.json: {e}"))
    }

    pub fn to_json(&self) -> String {
        // The Python launcher writes indent=2 and a trailing newline.
        let mut out = serde_json::to_string_pretty(self).expect("settings serialize");
        out.push('\n');
        out
    }

    /// Validation used before a launch: empty paths and unknown values produce actionable
    /// messages instead of starting a doomed process.
    pub fn problems(&self) -> Vec<String> {
        let mut out = Vec::new();
        if self.game_dir.trim().is_empty() && self.game_dir_for(self.start).trim().is_empty() {
            out.push("Set the game folder (the one with eboot.bin) on the Game page.".into());
        }
        if !["u1", "u2", "u3"].contains(&self.start.as_str()) {
            out.push(format!("Unknown game '{}': pick Uncharted 1, 2 or 3.", self.start));
        }
        if self.mouse_sensitivity.parse::<f32>().map_or(true, |v| v < 0.1 || v > 10.0) {
            out.push("Mouse sensitivity must be between 0.1 and 10.".into());
        }
        for (name, value, allowed) in [
            // The option lists mirror PRESENT_MODES and FRAME_RATES in the Python launcher.
            ("Presentation", &self.present_mode,
             ["Fifo", "FifoRelaxed", "Mailbox", "Immediate"].as_slice()),
            ("Frame rate", &self.frame_rate, ["60", "120", "144", "165", "240"].as_slice()),
            ("Anti-aliasing", &self.anti_aliasing,
             ["off", "taa", "fsr3", "fsr4", "fsr411", "dlss"].as_slice()),
            ("Frame generation", &self.frame_generation, ["off", "fsr3", "dlss"].as_slice()),
        ] {
            if !allowed.contains(&value.as_str()) {
                out.push(format!("{name} value '{value}' is not one of: {}", allowed.join(", ")));
            }
        }
        out
    }

    /// Where one game lives: its own folder when set (a game dumped on its own), else the
    /// collection folder. Mirrors `game_folder()` in the Python launcher.
    pub fn game_dir_for(&self, key: &str) -> &str {
        let own = match key {
            "u1" => &self.game_dir_u1,
            "u2" => &self.game_dir_u2,
            "u3" => &self.game_dir_u3,
            _ => "",
        };
        if !own.trim().is_empty() {
            own
        } else {
            &self.game_dir
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_match_the_python_launcher() {
        let s = Settings::default();
        assert_eq!(s.start, "u1");
        assert_eq!(s.present_mode, "Mailbox");
        assert_eq!(s.frame_rate, "60");
        assert_eq!(s.window_size, "1920x1080");
        assert_eq!(s.scaler, "fsr1");
        assert_eq!(s.sharpening, "500");
        assert_eq!(s.anti_aliasing, "off");
        assert_eq!(s.frame_generation, "off");
        assert_eq!(s.mouse_sensitivity, "1.0");
        assert!(s.mouse_look && s.check_updates);
    }

    #[test]
    fn unknown_keys_survive_a_round_trip() {
        let text = r#"{"start": "u2", "future_setting": {"a": 1}}"#;
        let s = Settings::from_json(text).unwrap();
        assert_eq!(s.start, "u2");
        let saved = s.to_json();
        assert!(saved.contains("future_setting"), "unknown key dropped: {saved}");
    }

    #[test]
    fn empty_file_gives_defaults() {
        assert_eq!(Settings::from_json("  ").unwrap(), Settings::default());
    }

    #[test]
    fn utf8_bom_is_tolerated_like_utf_8_sig() {
        let text = "\u{feff}{\"start\": \"u3\"}";
        assert_eq!(Settings::from_json(text).unwrap().start, "u3");
    }

    #[test]
    fn per_game_folder_wins() {
        let mut s = Settings::default();
        s.game_dir = "C:/collection".into();
        s.game_dir_u2 = "C:/u2-standalone".into();
        assert_eq!(s.game_dir_for("u1"), "C:/collection");
        assert_eq!(s.game_dir_for("u2"), "C:/u2-standalone");
    }

    #[test]
    fn problems_are_actionable() {
        let s = Settings::default();
        let problems = s.problems();
        assert!(problems.iter().any(|p| p.contains("game folder")), "{problems:?}");
        let mut ok = Settings::default();
        ok.game_dir = "C:/games/uncharted".into();
        assert!(ok.problems().is_empty(), "{:?}", ok.problems());
    }
}
