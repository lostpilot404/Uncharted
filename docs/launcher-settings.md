# Launcher settings

The packaged Tk launcher keeps one flat JSON object in
`%APPDATA%\uncharted-launcher\settings.json`. **The schema has not changed** during the
Play | Settings redesign: there is no version number, no rename and no destructive migration.
Older files are read as they are.

## Load, validate, save

- `load_settings()` merges `APP_DEFAULTS` with the saved object. A missing, unreadable,
  malformed or non-object file falls back to defaults (a warning is written to `launcher.log`).
  Unknown keys — including keys from a newer launcher — are preserved in memory.
- `normalize_settings()` repairs values **in memory** before any UI is built, so a corrupt
  binding dict or an out-of-range enum cannot crash the launcher. It is deterministic and
  idempotent: repeating it produces the same object and no new warnings.
  - Boolean defaults are coerced from `bool`/`0|1`/`true|false|yes|no|on|off` strings.
  - Every enumerated setting is checked against its option list. `anti_aliasing` also accepts
    FSR 4.1.1 and XeSS values saved by a package that shipped those assets, so a package
    without them still loads the file (it simply offers fewer choices).
  - `mouse_sensitivity` must be 0.1–10, `pad_vibration_strength` 0–100.
  - `extra_env` keeps only `NAME=value` tokens.
  - `key_bindings`, `pad_bindings` and `art_crops` must be objects.
  - Each repair is reported once in `launcher.log` and a single warning on startup.
- `collect()` writes the complete merged object on **Save settings**, on Play, when the interface
  language changes, and on normal close; it also mirrors `show_fps` into `bbport.ini` through
  `set_ini()`. `save_settings()` shows explicit success feedback or the list of repairs.
- The game never reads `settings.json`. `game_environment()` translates it into `BB_*`
  environment variables for `run.py`, and `write_input_ini()` writes `input.ini`. See
  `tests/test_launcher_ui.py` for load/save/repair regressions.

## Key map (58 keys)

Group names match the Settings sections; a key can be consumed by more than one path.

| Group | Keys (default) | Consumed by |
|---|---|---|
| Content | `game_dir` `game_dir_u1` `game_dir_u2` `game_dir_u3` `user_dir` (all `""`), `start` (`u1`), `language` (`1`), `player_name` (`""`) | `game_folder()`, `game_available()`, `game_environment()`, trophy/user paths |
| Display | `fullscreen` (`False`), `show_fps` (`False`), `present_mode` (`Mailbox`), `frame_rate` (`60`), `window_size` (`1920x1080`), `render_height` (`1080`), `aspect` (`16:9`), `scaler` (`fsr1`), `sharpening` (`500`) | `game_environment()` → `BB_*` env; `show_fps` also `set_ini()` |
| Graphics | `anti_aliasing` (`off`), `aa_preset` (`""`), `aa_sharpness` (`0.2`), `frame_generation` (`off`), `frame_gen_multiplier` (`2`) | `game_environment()` → upscaler/DLSS/FSR env |
| Audio | `speakers` (`auto`) | `game_environment()` → `BB_SPEAKERS` |
| Controls | `mouse_look` (`True`), `mouse_sensitivity` (`1.0`), `mouse_invert_y` (`False`), `pad_invert_y` (`False`), `pad_vibration` (`True`), `pad_vibration_strength` (`100`), `button_prompts` (`auto`), `key_bindings` (`{}`), `pad_bindings` (`{}`) | `write_input_ini()`, `game_environment()` |
| Fixes | `fix_u2_gpu_results` `fix_u2_mesh` `fix_tiny_readback` `fix_small_buffers` `fix_vram` `fix_job_sleep` `fix_thread_prio` (all `auto`) | `game_environment()` → per-fix toggles |
| Cheats | `u1_infinite_health` `u1_infinite_ammo` `u2_infinite_health` `u2_infinite_ammo` `u3_infinite_health` `u3_infinite_ammo` (all `False`) | `run.py` cheat patches |
| Advanced | `extra_env` (`""`), `close_on_play` (`False`), `check_updates` (`True`), `ui_language` (`""` = Windows locale), `shortcut_name` (`Uncharted: The Nathan Drake Collection`), `shortcut_icon` (`""`) | `game_environment()`, update check, desktop shortcut |
| Appearance / support | `art_banner` `art_u1` `art_u2` `art_u3` `steam_art` `shortcut_icon` (`""`), `art_crops` (`{}`), `trophy_game` (`u1`) | `uncharted_art.set_custom_art()`, Steam artwork, trophy window |

`art_*` values from older configurations are preserved on disk even if the user never opens
the Collection image editor. Diagnostics never include this file in full: only an allowlisted,
sanitized subset is exported (see [local-diagnostics.md](local-diagnostics.md)).
