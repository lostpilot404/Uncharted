//! uncharted-core: settings, game discovery, launch/update planning and diagnostics for the
//! Uncharted launcher. No UI and no async runtime here — everything is plain, testable code
//! the UI shell calls from worker threads.
//!
//! On-disk and process contracts are mirrored exactly from the current Python launcher
//! (launcher/uncharted_launcher.py, run.py): settings.json keys, input.ini lines, the
//! BB_* environment variables, param.sfo fields and the update preserve-list.

pub mod diagnostics;
pub mod discovery;
pub mod launch;
pub mod settings;
pub mod sfo;
pub mod update;
pub mod vram;
