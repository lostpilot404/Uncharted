//! Uncharted launcher for Windows 11 (egui/eframe). UI shell only: every piece of logic
//! lives in uncharted-core and is called from worker threads.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")] // no console window in release

mod app;
mod theme;

use app::LauncherApp;

fn main() -> eframe::Result {
    let options = eframe::NativeOptions {
        viewport: eframe::egui::ViewportBuilder::default()
            .with_inner_size([1180.0, 820.0])
            .with_min_inner_size([980.0, 700.0])
            .with_title("Uncharted — The Nathan Drake Collection for Windows"),
        ..Default::default()
    };
    eframe::run_native(
        "Uncharted",
        options,
        Box::new(|cc| Ok(Box::new(LauncherApp::new(cc)))),
    )
}
