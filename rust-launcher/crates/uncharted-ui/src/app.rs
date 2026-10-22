//! Application state and pages. UI-thread rules: no disk scans, no process waits and no
//! network on this thread — those run in [`spawn_worker`] and report back over a channel.

use crate::theme::{self, CARD, ERROR, GOLD, GOLD_HI, MUTED, TEXT, WARN};
use eframe::egui;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, Receiver, Sender};
use uncharted_core::discovery::{game_available, game_statuses, GameInfo, GameStatus};
use uncharted_core::launch::{game_environment, ini_path, input_ini, launch_command, set_ini};
use uncharted_core::settings::Settings;
use uncharted_core::vram::{low_vram_warning, RECOMMENDED_VRAM_MB};

#[derive(Debug)]
enum WorkerMsg {
    Gpu(Option<u64>),
    Discovery(Option<GameInfo>, Vec<GameStatus>),
    Log(String),
    GameOutput(String),
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
    msg_tx: Sender<WorkerMsg>,
    msg_rx: Receiver<WorkerMsg>,
    last_report: String,
    close_at: Option<std::time::Instant>,
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
        let (job_tx, msg_tx, msg_rx) = spawn_worker();
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
            msg_tx,
            msg_rx,
            last_report: String::new(),
            close_at: None,
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
        // collect() in the Python launcher also mirrors show_fps into bbport.ini.
        let ini = ini_path(&self.data_dir);
        if let Err(e) = set_ini(
            &ini,
            "show_fps",
            if self.settings.show_fps { "1" } else { "0" },
        ) {
            self.set_status(&format!("Could not update {}: {e}", ini.display()), true);
        }
    }

    /// Where the game's output log lives: the saves folder when set, else <data>/user
    /// (read_output() in the Python launcher).
    fn log_path(&self) -> PathBuf {
        let dir = if self.settings.user_dir.trim().is_empty() {
            self.data_dir.join("user")
        } else {
            PathBuf::from(self.settings.user_dir.trim())
        };
        dir.join("last_run.log")
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
                WorkerMsg::GameOutput(chunk) => {
                    // Live game output, kept to the same ~24 KiB the log viewer shows.
                    self.log_tail.push_str(&chunk);
                    let len = self.log_tail.len();
                    if len > 24 * 1024 {
                        self.log_tail.drain(..len - 24 * 1024);
                    }
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
                        let _ = self.job_tx.send(WorkerJob::Discover(self.settings.clone()));
                    }
                    ctx.request_repaint();
                }
                WorkerMsg::Report(text) => {
                    self.last_report = text.clone();
                    ctx.copy_text(text);
                    self.set_status(
                        "Redacted diagnostics report copied to the clipboard.",
                        false,
                    );
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
        // play() gates: settings must be sane and the chosen game must be in its folder
        // (game_available — a per-game dump may carry its elf as eboot.bin).
        let problems = self.settings.problems();
        if let Some(first) = problems.first() {
            self.set_status(first, true);
            return;
        }
        if !game_available(&self.settings, &self.settings.start) {
            self.set_status("This game is not in the chosen folder.", true);
            return;
        }
        self.save();
        // input.ini goes to the data dir; BB_INPUT_CONFIG points at it (write_input_ini).
        let input_path = self.data_dir.join("input.ini");
        if let Err(e) = std::fs::write(&input_path, input_ini(&self.settings)) {
            self.set_status(
                &format!("Could not write {}: {e}", input_path.display()),
                true,
            );
            return;
        }
        let (program, args) = launch_command(&self.port_dir());
        let env = game_environment(&self.settings, &input_path, &Default::default());
        let mut command = Command::new(&program);
        command.args(&args);
        for (key, value) in env {
            command.env(key, value);
        }
        // Popen(..., stdin=DEVNULL, stdout=PIPE, stderr=STDOUT, cwd=PORT_DIR) in the
        // Python launcher; both pipes feed the log (stderr read on its own thread).
        command.current_dir(self.port_dir());
        command.stdin(Stdio::null());
        command.stdout(Stdio::piped());
        command.stderr(Stdio::piped());
        match command.spawn() {
            Ok(mut child) => {
                let log_path = self.log_path();
                if let Some(parent) = log_path.parent() {
                    let _ = std::fs::create_dir_all(parent);
                }
                if let Some(out) = child.stdout.take() {
                    spawn_log_pump(out, log_path.clone(), self.msg_tx.clone());
                }
                if let Some(err) = child.stderr.take() {
                    spawn_log_pump(err, log_path, self.msg_tx.clone());
                }
                self.child = Some(child);
                self.play_state = PlayState::Running;
                self.log_tail.clear();
                self.set_status("Preparing the game; it opens in its own window…", false);
                if self.settings.close_on_play {
                    self.close_at =
                        Some(std::time::Instant::now() + std::time::Duration::from_secs(5));
                }
            }
            Err(e) => {
                self.set_status(&format!("Could not start {}: {e}", program.display()), true);
            }
        }
    }
}

impl eframe::App for LauncherApp {
    /// State work only (eframe 0.36: painting is not allowed in logic()).
    fn logic(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.drain_worker(ctx);
        self.poll_game();
        // close_on_play: the Python launcher closes itself five seconds after the game starts.
        if let Some(at) = self.close_at {
            if std::time::Instant::now() >= at {
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                self.close_at = None;
            }
        }
    }

    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        egui::Panel::top("title").show(ui, |ui| {
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

        egui::Panel::bottom("status").show(ui, |ui| {
            ui.add_space(6.0);
            ui.horizontal(|ui| {
                let color = if self.status_error { ERROR } else { MUTED };
                ui.label(egui::RichText::new(&self.status).color(color).size(13.0));
                if self.play_state == PlayState::Running {
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        ui.label(
                            egui::RichText::new("Game running")
                                .color(GOLD_HI)
                                .size(13.0),
                        );
                    });
                }
            });
            ui.add_space(6.0);
        });

        egui::Panel::left("nav")
            .resizable(false)
            .exact_size(170.0)
            .show(ui, |ui| {
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
                        .fill(if selected {
                            CARD
                        } else {
                            egui::Color32::TRANSPARENT
                        })
                        .min_size(egui::vec2(ui.available_width(), 36.0));
                    if ui.add(button).clicked() {
                        self.page = page;
                        if page == Page::Diagnostics {
                            let _ = self.job_tx.send(WorkerJob::ReadLog(self.log_path()));
                        }
                    }
                }
            });

        egui::CentralPanel::default().show(ui, |ui| {
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
            egui::RichText::new(
                "Choose the game, then press Play. The launcher never modifies \
                                 your settings or save data on its own.",
            )
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
                .fill(if selected {
                    CARD
                } else {
                    egui::Color32::TRANSPARENT
                })
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
                .color(if status.executable_present {
                    MUTED
                } else {
                    WARN
                })
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

        let playable = self.play_state == PlayState::Idle
            && game_available(&self.settings, &self.settings.start);
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
            (
                "game_dir",
                "Collection folder",
                self.settings.game_dir.clone(),
            ),
            (
                "game_dir_u1",
                "Uncharted 1 folder (optional)",
                self.settings.game_dir_u1.clone(),
            ),
            (
                "game_dir_u2",
                "Uncharted 2 folder (optional)",
                self.settings.game_dir_u2.clone(),
            ),
            (
                "game_dir_u3",
                "Uncharted 3 folder (optional)",
                self.settings.game_dir_u3.clone(),
            ),
            (
                "user_dir",
                "Saves folder (optional)",
                self.settings.user_dir.clone(),
            ),
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
            (
                "Presentation",
                "present_mode",
                ["Fifo", "FifoRelaxed", "Mailbox", "Immediate"].as_slice(),
            ),
            (
                "Frame rate",
                "frame_rate",
                ["60", "120", "144", "165", "240"].as_slice(),
            ),
            (
                "Window size",
                "window_size",
                [
                    "1280x720",
                    "1600x900",
                    "1920x1080",
                    "2560x1440",
                    "3840x2160",
                ]
                .as_slice(),
            ),
            ("Upscaling", "scaler", ["fsr1", "bilinear"].as_slice()),
            (
                "Sharpening",
                "sharpening",
                ["off", "1000", "500", "250", "0"].as_slice(),
            ),
            (
                "Anti-aliasing",
                "anti_aliasing",
                ["off", "taa", "fsr3", "fsr4", "fsr411", "dlss"].as_slice(),
            ),
            (
                "AA sharpness",
                "aa_sharpness",
                ["0", "0.2", "0.4", "0.7"].as_slice(),
            ),
            (
                "Frame generation",
                "frame_generation",
                ["off", "fsr3", "dlss"].as_slice(),
            ),
            (
                "Frame-gen multiplier",
                "frame_gen_multiplier",
                ["2", "3", "4"].as_slice(),
            ),
        ] {
            let current: &mut String = match field {
                "present_mode" => &mut self.settings.present_mode,
                "frame_rate" => &mut self.settings.frame_rate,
                "window_size" => &mut self.settings.window_size,
                "scaler" => &mut self.settings.scaler,
                "sharpening" => &mut self.settings.sharpening,
                "anti_aliasing" => &mut self.settings.anti_aliasing,
                "aa_sharpness" => &mut self.settings.aa_sharpness,
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
        if let Some(warning) = low_vram_warning(
            self.vram_mb,
            &self.settings.anti_aliasing,
            &self.settings.frame_generation,
        ) {
            ui.label(
                egui::RichText::new(format!("⚠ {warning}"))
                    .color(WARN)
                    .size(13.0),
            );
        } else if self.vram_mb.is_some() {
            ui.label(
                egui::RichText::new(format!("GPU: {}", self.gpu_line))
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
            let _ = self
                .job_tx
                .send(WorkerJob::BuildReport(self.settings.clone(), self.vram_mb));
            let _ = (settings_json, gpu_line, log_tail, user_profile, player);
        }
        if !self.last_report.is_empty() {
            ui.label(
                egui::RichText::new("Last report copied:")
                    .color(MUTED)
                    .size(12.0),
            );
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
        egui::ScrollArea::vertical()
            .max_height(320.0)
            .show(ui, |ui| {
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

fn spawn_worker() -> (Sender<WorkerJob>, Sender<WorkerMsg>, Receiver<WorkerMsg>) {
    let (job_tx, job_rx) = mpsc::channel::<WorkerJob>();
    let (msg_tx, msg_rx) = mpsc::channel::<WorkerMsg>();
    let worker_tx = msg_tx.clone();
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
                            uncharted_core::vram::parse_gpu_memory(&String::from_utf8_lossy(
                                &out.stderr,
                            ))
                            .1
                        });
                    let _ = msg_tx.send(WorkerMsg::Gpu(mib));
                }
                WorkerJob::Discover(settings) => {
                    let folder = settings
                        .game_dir_for(settings.start.as_str())
                        .trim()
                        .to_string();
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
                    let gpu = vram
                        .map(|v| format!("{v} MiB of video memory"))
                        .unwrap_or_default();
                    let user_profile = std::env::var("USERPROFILE").unwrap_or_default();
                    // The log lives in the saves folder when set, else <data>/user.
                    let log_dir = if settings.user_dir.trim().is_empty() {
                        data_dir().join("user")
                    } else {
                        std::path::PathBuf::from(settings.user_dir.trim())
                    };
                    let log_tail =
                        std::fs::read_to_string(log_dir.join("last_run.log")).unwrap_or_default();
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
    (job_tx, worker_tx, msg_rx)
}

/// One pipe of the game process to the log file and the UI (read_output() in spirit:
/// every byte reaches last_run.log; the UI keeps a bounded tail).
fn spawn_log_pump(
    pipe: impl std::io::Read + Send + 'static,
    log_path: PathBuf,
    tx: Sender<WorkerMsg>,
) {
    std::thread::spawn(move || {
        use std::io::Read;
        let mut pipe = pipe;
        let mut buffer = [0u8; 4096];
        loop {
            match pipe.read(&mut buffer) {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    if let Ok(mut file) = std::fs::OpenOptions::new()
                        .create(true)
                        .append(true)
                        .open(&log_path)
                    {
                        use std::io::Write;
                        let _ = file.write_all(&buffer[..n]);
                    }
                    let chunk = String::from_utf8_lossy(&buffer[..n]).into_owned();
                    let _ = tx.send(WorkerMsg::GameOutput(chunk));
                }
            }
        }
    });
}
