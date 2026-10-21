# Uncharted launcher — Rust prototype (Windows 11)

Workstream 2 of the audit: a modern Windows 11 launcher prototype written in Rust, kept
independent of the runtime fixes (workstream 1 is in `gpu/` and `tests/`). It reuses the
**existing on-disk and process contracts** of `launcher/uncharted_launcher.py` so the two
launchers can coexist and be compared.

> **Status honesty:** the workspace **compiles and tests green on real Windows** (GitHub
> `windows-latest`, `cargo fmt --check` + `cargo test` + `cargo build --release`; see the
> Rust launcher CI run for the tip commit — the release `uncharted-launcher.exe` is uploaded
> as a CI artifact). What is still unverified is **runtime/UI behavior on a Windows desktop**
> (the CI runner is headless): cold start, idle RAM, high-DPI rendering, dialogs, and the
> feature-parity checklist below need the manual Win11 pass before this replaces the Python
> launcher. The benchmark table is a protocol, not results.

## Build and test (Windows 11)

Requirements: current stable Rust (MSRV of egui 0.36 is 1.95+; `rustup default stable`),
no other toolchains.

```
cd rust-launcher
cargo test                 # uncharted-core unit tests (settings, SFO, launch env, update safety, vram)
cargo run -p uncharted-ui  # development run
cargo build --release      # target\release\uncharted-launcher.exe
```

Place `uncharted-launcher.exe` next to `Uncharted.exe`/`bin\` in the packaged port folder
(the probe `bin\bb-gpu-capabilities.exe` is used for the VRAM check; `--run` passes through
to `run.py` exactly like the current launcher).

## Framework comparison (official sources, retrieved 2026-10-21)

| Criterion (as required) | **egui/eframe 0.36** (chosen) | Slint 1.18 | Tauri 2.0 |
|---|---|---|---|
| Sources | egui.rs, docs.rs/eframe, crates.io/egui (2026-10-21) | slint.dev (2026-10-21) | tauri.app (2026-10-21) |
| License vs this GPL-2.0-or-later tree | MIT OR Apache-2.0 — compatible | GPL-3.0 **or** royalty-free custom or commercial (GPL-3 would rely on the “or later” clause; the custom license needs review) | MIT OR Apache-2.0 |
| Windows 11 target | native (winit; wgpu default renderer since eframe 0.34) | native | native via WebView2 (preinstalled on Win11) |
| Extra build tooling | **cargo only** | cargo + slint-build codegen | cargo + npm frontend + WebView2 SDK |
| High-DPI | winit per-monitor DPI | per-monitor DPI | webview DPI |
| UI style | custom-rendered (fits a restrained premium dark theme) | declarative DSL, Material components | HTML/CSS (most flexible, web-stack maintenance) |
| Binary-size class (documented, **not measured here**) | small–medium single exe | small (“runtime < 300 KiB RAM”, compiled UI) | “as little as 600 KB” + WebView2 |
| Known caveats | glow backend had RDP/VM issues (mitigated by wgpu default); UI is drawn, not native widgets | license choice required | Node toolchain in the release pipeline; IPC surface |

**Decision:** egui/eframe — license-safe with no relicense question, `cargo build --release`
is the entire pipeline (nothing to add to `packaging/windows/package.sh`), worker-thread
patterns map directly onto the “never block the UI thread” requirement, and its custom
drawing supports the restrained near-black/gold theme already used by the Python launcher
(palette ported in `crates/uncharted-ui/src/theme.rs`). Slint is the runner-up if a
declarative DSL is preferred and the royalty-free license is accepted; Tauri if a web-stack
team will maintain the UI. **Re-evaluate with the three measurements below on real hardware
before replacing the shipping launcher.**

## Benchmark protocol (run on the RTX 2050 laptop, record before/after)

1. Cold startup: `Measure-Command { .\uncharted-launcher.exe }` until first paint (or stopwatch), Python launcher likewise (`Uncharted.exe`).
2. Idle RAM: Task Manager private bytes 30 s after open, no interaction.
3. Release size: `dir target\release\uncharted-launcher.exe` and the packaged folder delta.
4. Responsiveness: drag the window during a GPU probe and a log load; type in folder fields during discovery.

## Feature parity checklist (vs launcher/uncharted_launcher.py)

| Feature | Status in prototype | Where |
|---|---|---|
| U1/U2/U3 selection cards | implemented | `app.rs` page_play |
| Game folder detection + validation (param.sfo TITLE/TITLE_ID/APP_VER) | implemented | `discovery.rs`, `sfo.rs` |
| `game_available()` rule (canonical exe **or** `eboot.bin` in a per-game folder) | implemented | `discovery.rs::game_available` |
| Per-game folders (BB_GAME_DIR_U1/U2/U3), user/saves folder | implemented | `settings.rs`, `page_game` |
| Play with actionable error states (gates mirror `play()`: problems + game_available) | implemented (spawn + exit codes) | `app.rs::play` |
| Graphics settings with the **source's exact option lists** (PRESENT_MODES incl. FifoRelaxed; FRAME_RATES 60/120/144/165/240; WINDOW_SIZES; SCALERS; SHARPENING off/1000/500/250/0; ANTI_ALIASING + fsr411; AA_SHARPNESS 0/0.2/0.4/0.7; FRAME_GENERATION; multipliers) | implemented | `page_display` |
| VRAM-aware guidance (< 6 GB), never auto-changing settings | implemented | `vram.rs`, page_display/page_diagnostics |
| Settings persistence in `%APPDATA%\uncharted-launcher\settings.json` (same schema and key order; UTF-8 BOM tolerated like `utf-8-sig`; unknown keys preserved) | implemented | `settings.rs` |
| `show_fps` mirrored into bbport.ini (`set_ini`, `BB_CONFIG` override honored) | implemented | `launch.rs::set_ini`, `app.rs::save` |
| input.ini generation (exact BUTTON_ACTIONS/STICK_ACTIONS actions and defaults; `mouse_sensitivity` `:.2f` clamp) | implemented | `launch.rs` |
| BB_* launch environment (exact contract of `game_environment()` incl. PYTHONUNBUFFERED/PYTHONIOENCODING/PYTHONUTF8) | implemented | `launch.rs` |
| Game output captured to `<user_dir or data>/user/last_run.log` + live tail (`read_output`) | implemented | `app.rs::spawn_log_pump` |
| `close_on_play` (close launcher 5 s after the game starts) | implemented | `app.rs::update` |
| Paths with spaces/Unicode; explicit args, never shell strings | implemented | `launch.rs`, `app.rs` |
| Update check/install (GitHub release zip, USER_FILES preserved, `version_tuple` digit-run ordering, path-traversal-safe) | planning + safety rules implemented; download/install execution **deferred** (needs Win11 test of the existing `--install-update` flow) | `update.rs` |
| Log tail + redacted diagnostics export | implemented | `diagnostics.rs`, page_diagnostics |
| Keyboard-accessible navigation | partial (egui tab order); screen-reader/a11y **deferred** |
| Translations (ru) | **deferred** (English only, matching `_()` identity stub of the current launcher) |
| Controls remapping UI | **deferred** (bindings written with correct defaults; UI editors not ported) |
| Steam shortcuts, FSR4 asset downloader, covers/art, in-game language list UI | **deferred** (unchanged Python launcher remains the shipping UI) |

Deliberate difference: a failed `input.ini` write **stops the launch with a visible error** (the Python launcher launches anyway without `BB_INPUT_CONFIG`) — the prototype favors actionable failure states over silent degradation.

## Safety notes

- Updates: upstream releases publish **no signatures or asset hashes** — the prototype does
  not fabricate verification. `update.rs` enforces archive entry safety (no traversal,
  absolute paths, drive prefixes) and the USER_FILES preserve-list, and stages downloads
  outside the install folder. Add hash pinning only if upstream publishes hashes.
- The prototype never modifies save data, game files, or settings without an explicit user
  action (the VRAM advice is a visible hint only).
- `#![windows_subsystem = "windows"]` in release; debug builds keep a console for logs.

## Layout

```
crates/uncharted-core   pure logic + unit tests (no UI, no async runtime)
crates/uncharted-ui     egui/eframe shell; all disk/process/network work on a worker thread
```
