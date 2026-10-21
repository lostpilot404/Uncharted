//! Launching the game: the environment contract of `game_environment()` in
//! launcher/uncharted_launcher.py, the input.ini format of `write_input_ini()`, and the
//! process command for run.py / the packaged --run entry. Everything is built as
//! (program, args, env) — never a shell string, so paths with spaces and non-ASCII
//! characters (C:\Игры\..., D:\Games\Uncharted Collection\) survive intact.

use crate::settings::Settings;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// The input.ini lines for the Controls page. Mirrors `write_input_ini()`.
pub fn input_ini(settings: &Settings) -> String {
    let sensitivity: f32 = settings.mouse_sensitivity.parse().unwrap_or(1.0).clamp(0.1, 10.0);
    let mut lines = vec![
        "# Uncharted controls (written by the launcher, Controls page)".to_string(),
        format!("mouse_look={}", i32::from(settings.mouse_look)),
        format!("mouse_sensitivity={sensitivity:.2}"),
        format!("mouse_invert_y={}", i32::from(settings.mouse_invert_y)),
        format!("pad_invert_y={}", i32::from(settings.pad_invert_y)),
    ];
    for (action, default_key, default_pad) in BINDINGS {
        let key = settings
            .key_bindings
            .get(*action)
            .map(String::as_str)
            .unwrap_or(default_key);
        lines.push(format!("key.{action}={key}"));
        if !default_pad.is_empty() {
            // The Python launcher writes pad.<action> for BUTTON_ACTIONS only (no stick rows).
            let pad = settings
                .pad_bindings
                .get(*action)
                .map(String::as_str)
                .unwrap_or(default_pad);
            lines.push(format!("pad.{action}={pad}"));
        }
    }
    let mut out = lines.join("\n");
    out.push('\n');
    out
}

/// Controls (input.ini, read by the game: src/runtime_pad.c). Mirrors BUTTON_ACTIONS and
/// STICK_ACTIONS in launcher/uncharted_launcher.py: (action, default key, default SDL button).
/// "A|B" in a key default means two bindings.
pub const BINDINGS: &[(&str, &str, &str)] = &[
    ("cross", "Space", "a"),
    ("circle", "C", "b"),
    ("square", "F", "x"),
    ("triangle", "E", "y"),
    ("l1", "Q", "leftshoulder"),
    ("r1", "G", "rightshoulder"),
    ("l2", "Mouse Right", "lefttrigger"),
    ("r2", "Mouse Left", "righttrigger"),
    ("l3", "Left Shift", "leftstick"),
    ("r3", "V", "rightstick"),
    ("options", "Escape|Return", "start"),
    ("touchpad", "Tab", "back"),
    ("up", "Up", "dpup"),
    ("down", "Down", "dpdown"),
    ("left", "Left|Wheel Up", "dpleft"),
    ("right", "Right|Wheel Down", "dpright"),
    ("ls_up", "W", ""),
    ("ls_down", "S", ""),
    ("ls_left", "A", ""),
    ("ls_right", "D", ""),
    ("rs_up", "I", ""),
    ("rs_down", "K", ""),
    ("rs_left", "J", ""),
    ("rs_right", "L", ""),
];

/// Environment for the game process: the keys run.py and the runtime read.
/// Mirrors `game_environment()`; extra_env entries are "KEY=VALUE" separated by whitespace.
pub fn game_environment(
    settings: &Settings,
    input_ini_path: &Path,
    extra: &BTreeMap<String, String>,
) -> BTreeMap<String, String> {
    let mut env = BTreeMap::new();
    env.insert(
        "BB_INPUT_CONFIG".into(),
        input_ini_path.to_string_lossy().into_owned(),
    );
    env.insert(
        "BB_GAME_DIR".into(),
        settings.game_dir_for(settings.start.as_str()).to_string(),
    );
    for (key, _title, _exe) in crate::settings::GAMES {
        let own = match key {
            "u1" => &settings.game_dir_u1,
            "u2" => &settings.game_dir_u2,
            _ => &settings.game_dir_u3,
        };
        if !own.trim().is_empty() {
            env.insert(
                format!("BB_GAME_DIR_{}", key.to_uppercase()),
                settings.game_dir_for(key).to_string(),
            );
        }
    }
    env.insert("BB_EXE".into(), settings.start.clone());
    if !settings.user_dir.trim().is_empty() {
        env.insert("BB_USER_DIR".into(), settings.user_dir.clone());
    }
    env.insert("BB_LANGUAGE".into(), settings.language.clone());
    if !settings.player_name.trim().is_empty() {
        env.insert(
            "BB_USER_NAME".into(),
            settings.player_name.trim().to_string(),
        );
    }
    env.insert(
        "BB_FULLSCREEN".into(),
        if settings.fullscreen { "1" } else { "0" }.into(),
    );
    env.insert("BB_PRESENT_MODE".into(), settings.present_mode.clone());
    env.insert("BB_VBLANK_HZ".into(), settings.frame_rate.clone());
    env.insert("BB_WINDOW_SIZE".into(), settings.window_size.clone());
    env.insert(
        "BB_FSR1".into(),
        if settings.scaler == "fsr1" { "1" } else { "0" }.into(),
    );
    if settings.sharpening != "off" {
        env.insert("BB_RCAS".into(), "1".into());
        env.insert(
            "BB_RCAS_ATTENUATION".into(),
            settings.sharpening.clone(),
        );
    } else {
        env.insert("BB_RCAS".into(), "0".into());
    }
    env.insert("BB_UPSCALER".into(), settings.anti_aliasing.clone());
    env.insert("BB_FSR_SHARPNESS".into(), settings.aa_sharpness.clone());
    env.insert("BB_FRAME_GEN".into(), settings.frame_generation.clone());
    env.insert(
        "BB_FRAME_GEN_MULTIPLIER".into(),
        settings.frame_gen_multiplier.clone(),
    );
    for item in settings.extra_env.split_whitespace() {
        if let Some((key, value)) = item.split_once('=') {
            env.insert(key.to_string(), value.to_string());
        }
    }
    for (key, value) in extra {
        env.insert(key.clone(), value.clone());
    }
    env
}

/// The game process to start: packaged `Uncharted.exe --run` next to the launcher, or a
/// development tree's python + run.py. Returns the program and its arguments (the caller
/// spawns with an explicit argument list — never a joined command line).
pub fn launch_command(port_dir: &Path) -> (PathBuf, Vec<String>) {
    let packaged = port_dir.join("Uncharted.exe");
    if packaged.is_file() {
        return (packaged, vec!["--run".to_string()]);
    }
    (
        PathBuf::from("python"),
        vec![
            port_dir.join("run.py").to_string_lossy().into_owned(),
        ],
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn settings() -> Settings {
        let mut s = Settings::default();
        s.game_dir = "D:\\Игры\\Uncharted Collection".into();
        s.mouse_sensitivity = "5".into();
        s
    }

    #[test]
    fn input_ini_has_every_binding() {
        let text = input_ini(&settings());
        for (action, _key, pad) in BINDINGS {
            assert!(text.contains(&format!("key.{action}=")), "missing key.{action}");
            if !pad.is_empty() {
                assert!(text.contains(&format!("pad.{action}=")), "missing pad.{action}");
            }
        }
        // Stick directions have keyboard bindings but no controller buttons of their own.
        assert!(!text.contains("pad.ls_up="));
        assert!(text.contains("key.cross=Space"));
        assert!(text.contains("pad.cross=a"));
        assert!(text.contains("mouse_look=1"));
        // Sensitivity is clamped to the slider's range and formatted stably.
        assert!(text.contains("mouse_sensitivity=5.00"));
    }

    #[test]
    fn environment_carries_the_contract() {
        let env = game_environment(&settings(), Path::new("C:\\cfg\\input.ini"), &BTreeMap::new());
        assert_eq!(env["BB_EXE"], "u1");
        assert_eq!(env["BB_GAME_DIR"], "D:\\Игры\\Uncharted Collection");
        assert_eq!(env["BB_PRESENT_MODE"], "Mailbox");
        assert_eq!(env["BB_VBLANK_HZ"], "60");
        assert_eq!(env["BB_UPSCALER"], "off");
        assert_eq!(env["BB_FRAME_GEN"], "off");
        assert_eq!(env["BB_RCAS"], "1");
        assert_eq!(env["BB_RCAS_ATTENUATION"], "500");
        assert_eq!(env["BB_INPUT_CONFIG"], "C:\\cfg\\input.ini");
    }

    #[test]
    fn extra_env_entries_override() {
        let mut s = settings();
        s.extra_env = "BB_FRAME_STATS=1 BAD_TOKEN BB_TIMEOUT=5".into();
        let env = game_environment(&s, Path::new("input.ini"), &BTreeMap::new());
        assert_eq!(env["BB_FRAME_STATS"], "1");
        assert_eq!(env["BB_TIMEOUT"], "5");
        assert!(!env.contains_key("BAD_TOKEN"));
    }

    #[test]
    fn paths_with_spaces_are_single_arguments() {
        let (program, args) = launch_command(Path::new("D:\\My Games\\Uncharted"));
        // Without a packaged exe this is the dev-tree form: one argument, unsplit.
        assert_eq!(args.len(), 1);
        assert!(args[0].ends_with("run.py"));
        assert!(program.to_string_lossy().contains("python"));
    }
}
