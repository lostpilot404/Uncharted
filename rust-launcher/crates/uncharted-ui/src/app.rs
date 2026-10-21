//! Application state and pages. UI-thread rules: no disk scans, no process waits and no
//! network on this thread — those run in [`spawn_worker`] and report back over a channel.

use crate::theme::{self, CARD, ERROR, GOLD, GOLD_HI, MUTED, TEXT, WARN};
use eframe::egui;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::mpsc::{self, Receiver, Sender};
use uncharted_core::discovery::{game_statuses, GameInfo, GameStatus};
use uncharted_core::launch::{game_environment, input_ini, launch_command};
use uncharted_core::settings::Settings;
use uncharted_core::vram::{low_vram_warning, RECOMMENDED_VRAM_MB};

#[derive(Debug)]
enum WorkerMsg {
    Gpu(Option<u64>),
    Discovery(Option<GameInfo>, Vec<GameStatus>),
    Log(String),
    ChosenFolder(Option<PathBuf>, &'static str),
    Report(String),
}

#[derive(Debug)]
enum WorkerJob {
    ProbeGpu(PathBuf),
    Discover(Settings),
    ReadLog(PathBuf),
    PickFolder(&'static str),
    BuildReport(Settings, Option<u64>),
}

pub struct LauncherApp {
    settings: Settings,
    data_dir: PathBuf,
    page: Page,
    game_info: Option<GameInfo>,
    statuses: Vec<GameStatus>,
    vram_mb: Option<u64>,
    gpu_line: String,
    log_tail: String,
    status: String,
    status_error: bool,
    play_state: PlayState,
    child: Option<Child>,
    job_tx: Sender<WorkerJob>,
    msg_rx: Receiver<WorkerMsg>,
    last_report: String,
}

#[derive(Debug, PartialEq)]
enum PlayState {
    Idle,
    Running,
}

#[derive(Debug, PartialEq, Clone, Copy)]
enum Page {
    Play,
    Game,
    Display,
    Diagnostics,
}

impl LauncherApp {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        theme::apply(&cc.egui_ctx);
        let data_dir = data_dir();
        let settings = load_settings(&data_dir);
        let (job_tx, msg_rx) = spawn_worker();
        let app = Self {
            settings,
            data_dir,
            page: Page::Play,
            game_info: None,
            statuses: Vec::new(),
            vram_mb: None,
            gpu_line: "not checked yet".into(),
            log_tail: String::new(),
            status: "Starting…".into(),
            status_error: false,
            play_state: PlayState::Idle,
            child: None,
            job_tx,
            msg_rx,
            last_report: String::new(),
        };
        let _ = app.job_tx.send(WorkerJob::ProbeGpu(app.port_dir()));
        let _ = app.job_tx.send(WorkerJob::Discover(app.settings.clone()));
        app
    }

    fn port_dir(&self) -> PathBuf {
        std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(|d| d.to_path_buf()))
            .unwrap_or_else(|| PathBuf::from("."))
    }

    fn save(&mut self) {
        let path = settings_path(&self.data_dir);
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        // Write-then-rename so a crash mid-save cannot corrupt the settings.
        let tmp = path.with_extension("json.tmp");
        match std::fs::write(&tmp, self.settings.to_json()) {
            Ok(()) => {
                if let Err(e) = std::fs::rename(&tmp, &path) {
                    self.set_status(&format!("Could not save settings: {e}"), true);
                }
            }
            Err(e) => self.set_status(&format!("Could not save settings: {e}"), true),
        }
    }

    fn set_status(&mut self, text: &str, error: bool) {
        self.status = text.to_string();
        self.status_error = error;
    }

    fn drain_worker(&mut self, ctx: &egui::Context) {
        while let Ok(msg) = self.msg_rx.try_recv() {
            match msg {
                WorkerMsg::Gpu(mib) => {
                    self.vram_mb = mib;
                    self.gpu_line = match mib {
                        Some(v) => format!("{v} MiB of video memory"),
                        None => "unknown (bb-gpu-capabilities did not answer)".into(),
                    };
                    ctx.request_repaint();
                }
                WorkerMsg::Discovery(info, statuses) => {
                    self.game_info = info;
                    self.statuses = statuses;
                    ctx.request_repaint();
                }
                WorkerMsg::Log(text) => {
                    self.log_tail = text;
                    ctx.request_repaint();
                }
                WorkerMsg::ChosenFolder(path, field) => {
                    if let Some(path) = path {
                        let value = path.to_string_lossy().into_owned();
                        match field {
                            "game_dir" => self.settings.game_dir = value,
                            "game_dir_u1" => self.settings.game_dir_u1 = value,
                            "game_dir_u2" => self.settings.game_dir_u2 = value,
                            "game_dir_u3" => self.settings.game_dir_u3 = value,
                            "user_dir" => self.settings.user_dir = value,
                            _ => {}
                        }
                        self.save();
                        let _ = self
                            .job_tx
                            .send(WorkerJob::Discover(self.settings.clone()));
                    }
                    ctx.request_repaint();
                }
                WorkerMsg::Report(text) => {
                    self.last_report = text.clone();
                    ctx.copy_text(text);
                    self.set_status("Redacted diagnostics report copied to the clipboard.", false);
                    ctx.request_repaint();
                }
            }
        }
    }

    fn poll_game(&mut self) {
        if let Some(child) = self.child.as_mut() {
            match child.try_wait() {
                Ok(Some(status)) => {
                    self.child = None;
                    self.play_state = PlayState::Idle;
                    self.set_status(&format!("The game exited ({status})."), false);
                }
                Ok(None) => {}
                Err(e) => {
                    self.child = None;
                    self.play_state = PlayState::Idle;
                    self.set_status(&format!("Lost track of the game process: {e}"), true);
                }
            }
        }
    }

    fn play(&mut self) {
        if self.play_state == PlayState::Running {
            return;
        }
        let problems = self.settings.problems();
        if let Some(first) = problems.first() {
            self.set_status(first, true);
            return;
        }
        self.save();
        // input.ini goes to the data dir; BB_INPUT_CONFIG points at it (write_input_ini).
        let input_path = self.data_dir.join("input.ini");
        if let Err(e) = std::fs::write(&input_path, input_ini(&self.settings)) {
            self.set_status(&format!("Could not write {}: {e}", input_path.display()), true);
            return;
        }
        let (program, args) = launch_command(&self.port_dir());
        let env = game_environment(&self.settings, &input_path, &Default::default());
        let mut command = Command::new(&program);
        command.args(&args);
        for (key, value) in env {
            command.env(key, value);
        }
        match command.spawn() {
            Ok(child) => {
                self.child = Some(child);
                self.play_state = PlayState::Running;
                self.set_status(
                    &format!("Playing Uncharted {}…", self.settings.start.to_uppercase()),
                    false,
                );
            }
            Err(e) => {
                self.set_status(
                    &format!("Could not start {}: {e}", program.display()),
                    true,
                );
            }
        }
    }
}

impl eframe::App for LauncherApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.drain_worker(ctx);
        self.poll_game();

        egui::TopBottomPanel::top("title").show(ctx, |ui| {
            ui.add_space(10.0);
            ui.horizontal(|ui| {
                ui.label(
                    egui::RichText::new("UNCHARTED")
                        .color(GOLD)
                        .size(20.0)
                        .strong(),
                );
                ui.label(
                    egui::RichText::new("The Nathan Drake Collection for Windows")
                        .color(MUTED)
                        .size(13.0),
                );
            });
            ui.add_space(6.0);
        });

        egui::TopBottomPanel::bottom("status").show(ctx, |ui| {
            ui.add_space(6.0);
            ui.horizontal(|ui| {
                let color = if self.status_error { ERROR } else { MUTED };
                ui.label(egui::RichText::new(&self.status).color(color).size(13.0));
                if self.play_state == PlayState::Running {
                    ui.with_layout(
                        egui::Layout::right_to_left(egui::Align::Center),
                        |ui| {
                            ui.label(
                                egui::RichText::new("Game running")
                                    .color(GOLD_HI)
                                    .size(13.0),
                            );
                        },
                    );
                }
            });
            ui.add_space(6.0);
        });

        egui::SidePanel::left("nav")
            .resizable(false)
            .exact_width(170.0)
            .show(ctx, |ui| {
                ui.add_space(12.0);
                for (page, label) in [
                    (Page::Play, "Play"),
                    (Page::Game, "Game"),
                    (Page::Display, "Display"),
                    (Page::Diagnostics, "Diagnostics"),
                ] {
                    let selected = self.page == page;
                    let text = egui::RichText::new(label)
                        .color(if selected { GOLD_HI } else { TEXT })
                        .size(15.0);
                    let button = egui::Button::new(text)
                        .fill(if selected { CARD } else { egui::Color32::TRANSPARENT })
                        .min_size(egui::vec2(ui.available_width(), 36.0));
                    if ui.add(button).clicked() {
                        self.page = page;
                        if page == Page::Diagnostics {
                            let _ = self
                                .job_tx
                                .send(WorkerJob::ReadLog(self.data_dir.join("last_run.log")));
                        }
                    }
                }
            });

        egui::CentralPanel::default().show(ctx, |ui| {
            egui::ScrollArea::vertical().show(ui, |ui| match self.page {
                Page::Play => self.page_play(ui),
                Page::Game => self.page_game(ui),
                Page::Display => self.page_display(ui),
                Page::Diagnostics => self.page_diagnostics(ui),
            });
        });
    }
}

impl LauncherApp {
    fn page_play(&mut self, ui: &mut egui::Ui) {
        ui.heading("Play");
        ui.add_space(4.0);
        ui.label(
            egui::RichText::new("Choose the game, then press Play. The launcher never modifies \
                                 your settings or save data on its own.")
                .color(MUTED),
        );
        ui.add_space(8.0);

        let statuses = self.statuses.clone();
        for status in &statuses {
            let selected = self.settings.start == status.key;
            let title = match status.key.as_str() {
                "u1" => "Uncharted 1 · Drake's Fortune",
                "u2" => "Uncharted 2 · Among Thieves",
                _ => "Uncharted 3 · Drake's Deception",
            };
            let mut text = egui::RichText::new(title).size(16.0);
            if selected {
                text = text.color(GOLD_HI).strong();
            }
            let card = egui::Button::new(text)
                .fill(if selected { CARD } else { egui::Color32::TRANSPARENT })
                .min_size(egui::vec2(ui.available_width(), 52.0));
            let resp = ui.add(card);
            if resp.clicked() {
                self.settings.start = status.key.clone();
                self.save();
                let _ = self.job_tx.send(WorkerJob::Discover(self.settings.clone()));
            }
            ui.label(
                egui::RichText::new(if status.executable_present {
                    "ready in the selected folder"
                } else {
                    "executable not found in the selected folder (Game page)"
                })
                .color(if status.executable_present { MUTED } else { WARN })
                .size(12.5),
            );
        }

        ui.add_space(10.0);
        let ready = match &self.game_info {
            Some(info) => format!("{} · {} · version {}", info.title, info.id, info.version),
            None => "Choose the game folder (the one with eboot.bin) on the Game page.".to_string(),
        };
        ui.label(egui::RichText::new(ready).color(MUTED).size(13.0));
        ui.add_space(10.0);

        let playable = self.game_info.is_some() && self.play_state == PlayState::Idle;
        let play = egui::Button::new(
            egui::RichText::new(if self.play_state == PlayState::Running {
                "Playing…"
            } else {
                "Play"
            })
            .size(18.0)
            .strong(),
        )
        .fill(if playable { GOLD } else { CARD })
        .min_size(egui::vec2(220.0, 52.0));
        if ui.add_enabled(playable, play).clicked() {
            self.play();
        }
    }

    fn page_game(&mut self, ui: &mut egui::Ui) {
        ui.heading("Game");
        ui.add_space(4.0);
        for (field, label, value) in [
            ("game_dir", "Collection folder", self.settings.game_dir.clone()),
            ("game_dir_u1", "Uncharted 1 folder (optional)", self.settings.game_dir_u1.clone()),
            ("game_dir_u2", "Uncharted 2 folder (optional)", self.settings.game_dir_u2.clone()),
            ("game_dir_u3", "Uncharted 3 folder (optional)", self.settings.game_dir_u3.clone()),
            ("user_dir", "Saves folder (optional)", self.settings.user_dir.clone()),
        ] {
            theme::section(ui, label);
            ui.horizontal(|ui| {
                let mut text = value;
                let resp = ui.add(
                    egui::TextEdit::singleline(&mut text)
                        .desired_width(ui.available_width() - 90.0)
                        .hint_text("D:\\Игры\\Uncharted Collection"),
                );
                if resp.changed() {
                    match field {
                        "game_dir" => self.settings.game_dir = text,
                        "game_dir_u1" => self.settings.game_dir_u1 = text,
                        "game_dir_u2" => self.settings.game_dir_u2 = text,
                        "game_dir_u3" => self.settings.game_dir_u3 = text,
                        "user_dir" => self.settings.user_dir = text,
                        _ => {}
                    }
                    self.save();
                }
                if ui.button("Browse…").clicked() {
                    let _ = self.job_tx.send(WorkerJob::PickFolder(field));
                }
            });
        }
        theme::section(ui, "Validation");
        if self.statuses.is_empty() {
            ui.label(egui::RichText::new("No folder set yet.").color(MUTED));
        }
        for status in self.statuses.clone() {
            let (icon, color) = if status.executable_present {
                ("✓", GOLD_HI)
            } else {
                ("✗", ERROR)
            };
            ui.label(
                egui::RichText::new(format!(
                    "{icon} {}: {}",
                    status.title,
                    status.folder.as_deref().unwrap_or("(no folder)")
                ))
                .color(color)
                .size(13.0),
            );
        }
    }

    fn page_display(&mut self, ui: &mut egui::Ui) {
        ui.heading("Display");
        ui.add_space(4.0);
        let mut changed = false;
        for (label, field, options) in [
            ("Presentation", "present_mode", ["Fifo", "Mailbox", "Immediate"].as_slice()),
            ("Frame rate", "frame_rate", ["30", "60", "120", "240"].as_slice()),
            ("Window size", "window_size", ["1280x720", "1600x900", "1920x1080", "2560x1440", "3840x2160"].as_slice()),
            ("Upscaling", "scaler", ["fsr1", "bilinear"].as_slice()),
            ("Sharpening", "sharpening", ["off", "250", "500", "750"].as_slice()),
            ("Anti-aliasing", "anti_aliasing", ["off", "taa", "fsr3", "fsr4", "fsr411", "dlss"].as_slice()),
            ("Frame generation", "frame_generation", ["off", "fsr3", "dlss"].as_slice()),
            ("Frame-gen multiplier", "frame_gen_multiplier", ["2", "3", "4"].as_slice()),
        ] {
            let current: &mut String = match field {
                "present_mode" => &mut self.settings.present_mode,
                "frame_rate" => &mut self.settings.frame_rate,
                "window_size" => &mut self.settings.window_size,
                "scaler" => &mut self.settings.scaler,
                "sharpening" => &mut self.settings.sharpening,
                "anti_aliasing" => &mut self.settings.anti_aliasing,
                "frame_generation" => &mut self.settings.frame_generation,
                _ => &mut self.settings.frame_gen_multiplier,
            };
            ui.horizontal(|ui| {
                ui.label(egui::RichText::new(label).color(TEXT).size(14.0));
                egui::ComboBox::from_id_salt(field)
                    .selected_text(current.as_str())
                    .width(220.0)
                    .show_ui(ui, |ui| {
                        for option in options {
                            changed |= ui
                                .selectable_value(&mut *current, (*option).to_string(), *option)
                                .changed();
                        }
                    });
            });
        }
        if changed {
            self.save();
        }
        ui.add_space(6.0);
        if let Some(warning) =
            low_vram_warning(self.vram_mb, &self.settings.anti_aliasing, &self.settings.frame_generation)
        {
            ui.label(egui::RichText::new(format!("⚠ {warning}")).color(WARN).size(13.0));
        } else if self.vram_mb.is_some() {
            ui.label(
                egui::RichText::new(format!(
                    "GPU: {}",
                    self.gpu_line
                ))
                .color(MUTED)
                .size(12.5),
            );
        }
    }

    fn page_diagnostics(&mut self, ui: &mut egui::Ui) {
        ui.heading("Diagnostics");
        ui.add_space(4.0);
        ui.label(
            egui::RichText::new(format!(
                "GPU: {}. The README recommends 6 GB of video memory or more; {} MiB cards \
                 should keep Anti-aliasing and Frame generation off.",
                self.gpu_line, RECOMMENDED_VRAM_MB
            ))
            .color(MUTED)
            .size(13.0),
        );
        ui.add_space(6.0);
        if ui.button("Copy redacted report").clicked() {
            let settings_json = self.settings.to_json();
            let gpu_line = self.gpu_line.clone();
            let log_tail = self.log_tail.clone();
            let user_profile = std::env::var("USERPROFILE").unwrap_or_default();
            let player = self.settings.player_name.clone();
            let _ = self.job_tx.send(WorkerJob::BuildReport(self.settings.clone(), self.vram_mb));
            let _ = (settings_json, gpu_line, log_tail, user_profile, player);
        }
        if !self.last_report.is_empty() {
            ui.label(egui::RichText::new("Last report copied:").color(MUTED).size(12.0));
            egui::ScrollArea::vertical()
                .max_height(220.0)
                .show(ui, |ui| {
                    ui.add(
                        egui::TextEdit::multiline(&mut self.last_report.as_str())
                            .desired_width(f32::INFINITY)
                            .font(egui::TextStyle::Monospace),
                    );
                });
        }
        theme::section(ui, "Log tail (last_run.log)");
        egui::ScrollArea::vertical().max_height(320.0).show(ui, |ui| {
            ui.add(
                egui::TextEdit::multiline(&mut self.log_tail.as_str())
                    .desired_width(f32::INFINITY)
                    .font(egui::TextStyle::Monospace),
            );
        });
    }
}

fn data_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("BB_DATA_DIR") {
        return PathBuf::from(dir);
    }
    if let Ok(appdata) = std::env::var("APPDATA") {
        return PathBuf::from(appdata).join("uncharted-launcher");
    }
    PathBuf::from("uncharted-launcher")
}

fn settings_path(data_dir: &std::path::Path) -> PathBuf {
    data_dir.join("settings.json")
}

fn load_settings(data_dir: &std::path::Path) -> Settings {
    std::fs::read_to_string(settings_path(data_dir))
        .ok()
        .and_then(|text| Settings::from_json(&text).ok())
        .unwrap_or_default()
}

fn spawn_worker() -> (Sender<WorkerJob>, Receiver<WorkerMsg>) {
    let (job_tx, job_rx) = mpsc::channel::<WorkerJob>();
    let (msg_tx, msg_rx) = mpsc::channel::<WorkerMsg>();
    std::thread::spawn(move || {
        while let Ok(job) = job_rx.recv() {
            match job {
                WorkerJob::ProbeGpu(port_dir) => {
                    let exe = port_dir.join("bin").join("bb-gpu-capabilities.exe");
                    let exe = if exe.is_file() {
                        exe
                    } else {
                        port_dir.join("out").join("bb-gpu-capabilities.exe")
                    };
                    let mib = Command::new(&exe)
                        .arg("--memory")
                        .output()
                        .ok()
                        .and_then(|out| {
                            uncharted_core::vram::parse_gpu_memory(
                                &String::from_utf8_lossy(&out.stderr),
                            )
                            .1
                        });
                    let _ = msg_tx.send(WorkerMsg::Gpu(mib));
                }
                WorkerJob::Discover(settings) => {
                    let folder = settings.game_dir_for(settings.start.as_str()).trim().to_string();
                    let info = if folder.is_empty() {
                        None
                    } else {
                        GameInfo::inspect(std::path::Path::new(&folder))
                    };
                    let _ = msg_tx.send(WorkerMsg::Discovery(info, game_statuses(&settings)));
                }
                WorkerJob::ReadLog(path) => {
                    let text = std::fs::read(&path)
                        .map(|bytes| {
                            // Tail only: the last ~24 KiB keeps the UI responsive.
                            let mut bytes = bytes;
                            if bytes.len() > 24 * 1024 {
                                bytes.drain(..bytes.len() - 24 * 1024);
                            }
                            String::from_utf8_lossy(&bytes).into_owned()
                        })
                        .unwrap_or_else(|e| format!("(no log yet: {e})"));
                    let _ = msg_tx.send(WorkerMsg::Log(text));
                }
                WorkerJob::PickFolder(field) => {
                    let picked = rfd::FileDialog::new().pick_folder();
                    let _ = msg_tx.send(WorkerMsg::ChosenFolder(picked, field));
                }
                WorkerJob::BuildReport(settings, vram) => {
                    let gpu = vram.map(|v| format!("{v} MiB of video memory")).unwrap_or_default();
                    let user_profile = std::env::var("USERPROFILE").unwrap_or_default();
                    let log_tail =
                        std::fs::read_to_string(data_dir().join("last_run.log")).unwrap_or_default();
                    let report = uncharted_core::diagnostics::report(
                        env!("CARGO_PKG_VERSION"),
                        &settings.to_json(),
                        &gpu,
                        &log_tail,
                        &user_profile,
                        &settings.player_name,
                    );
                    let _ = msg_tx.send(WorkerMsg::Report(report));
                }
            }
        }
    });
    (job_tx, msg_rx)
}
