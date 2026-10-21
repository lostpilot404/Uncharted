# Uncharted launcher — Rust prototype (Windows 11)

Workstream 2 of the audit: a modern Windows 11 launcher prototype written in Rust, kept
independent of the runtime fixes (workstream 1 is in `gpu/` and `tests/`). It reuses the
**existing on-disk and process contracts** of `launcher/uncharted_launcher.py` so the two
launchers can coexist and be compared.

> **Status honesty:** this prototype is **source-complete but not compiled in the audit
> environment** (no Rust toolchain is reachable from the sandbox: `static.rust-lang.org` is
> blocked and no GitHub/pypi/npm channel ships a toolchain). `cargo build`, `cargo test` and
> the benchmark table below are **required Win11 steps**, not completed results.

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
| Per-game folders (BB_GAME_DIR_U1/U2/U3), user/saves folder | implemented | `settings.rs`, `page_game` |
| Play with actionable error states | implemented (spawn + exit codes) | `app.rs::play` |
| Graphics settings the runtime supports (present/framerate/scaler/sharpening/AA/FG) | implemented (same value lists) | `page_display` |
| VRAM-aware guidance (< 6 GB), never auto-changing settings | implemented | `vram.rs`, page_display/page_diagnostics |
| Settings persistence in `%APPDATA%\uncharted-launcher\settings.json` (same schema; unknown keys preserved) | implemented | `settings.rs` |
| input.ini generation (exact actions/defaults of BUTTON_ACTIONS/STICK_ACTIONS) | implemented | `launch.rs` |
| BB_* launch environment (exact contract of game_environment()) | implemented | `launch.rs` |
| Paths with spaces/Unicode; explicit args, never shell strings | implemented | `launch.rs`, `app.rs` |
| Update check/install (GitHub release zip, USER_FILES preserved, path-traversal-safe) | planning + safety rules implemented; download/install execution **deferred** (needs Win11 test of the existing `--install-update` flow) | `update.rs` |
| Log tail + redacted diagnostics export | implemented | `diagnostics.rs`, page_diagnostics |
| Keyboard-accessible navigation | partial (egui tab order); screen-reader/a11y **deferred** |
| Translations (ru) | **deferred** (English only, matching `_()` identity stub of the current launcher) |
| Controls remapping UI | **deferred** (bindings written with correct defaults; UI editors not ported) |
| Steam shortcuts, FSR4 asset downloader, covers/art | **deferred** (unchanged Python launcher remains the shipping UI) |

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
