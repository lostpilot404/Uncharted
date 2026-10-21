//! Restrained premium dark theme: near-black base, raised panels, Uncharted gold accent.
//! Palette mirrors the current launcher (COLORS in launcher/uncharted_launcher.py) so the
//! two frontends look like the same product.

use egui::{Color32, Context, CornerRadius, FontFamily, FontId, Stroke, TextStyle, Visuals};

pub const BG: Color32 = Color32::from_rgb(0x08, 0x09, 0x0a);
pub const PANEL: Color32 = Color32::from_rgb(0x0c, 0x0d, 0x0f);
pub const SURFACE: Color32 = Color32::from_rgb(0x0f, 0x10, 0x12);
pub const CARD: Color32 = Color32::from_rgb(0x17, 0x18, 0x1b);
pub const CARD_HI: Color32 = Color32::from_rgb(0x20, 0x22, 0x26);
pub const LINE: Color32 = Color32::from_rgb(0x26, 0x28, 0x2c);
pub const TEXT: Color32 = Color32::from_rgb(0xf3, 0xef, 0xe6);
pub const MUTED: Color32 = Color32::from_rgb(0x8e, 0x8a, 0x82);
pub const GOLD: Color32 = Color32::from_rgb(0xe7, 0xc3, 0x6a);
pub const GOLD_HI: Color32 = Color32::from_rgb(0xf3, 0xd7, 0x8a);
pub const WARN: Color32 = Color32::from_rgb(0xd9, 0xa4, 0x41);
pub const ERROR: Color32 = Color32::from_rgb(0xe0, 0x7a, 0x4f);

/// Fonts, spacing and visuals. Called once at startup.
pub fn apply(ctx: &Context) {
    let mut visuals = Visuals::dark();
    visuals.panel_fill = PANEL;
    visuals.window_fill = SURFACE;
    visuals.extreme_bg_color = BG;
    visuals.faint_bg_color = CARD;
    visuals.selection.bg_fill = GOLD;
    visuals.selection.stroke = Stroke::new(1.0, GOLD_HI);
    visuals.widgets.noninteractive.bg_fill = CARD;
    visuals.widgets.noninteractive.fg_stroke = Stroke::new(1.0, MUTED);
    visuals.widgets.noninteractive.corner_radius = CornerRadius::same(6);
    visuals.widgets.inactive.bg_fill = CARD;
    visuals.widgets.inactive.fg_stroke = Stroke::new(1.0, TEXT);
    visuals.widgets.inactive.corner_radius = CornerRadius::same(6);
    visuals.widgets.hovered.bg_fill = CARD_HI;
    visuals.widgets.hovered.fg_stroke = Stroke::new(1.0, GOLD_HI);
    visuals.widgets.hovered.corner_radius = CornerRadius::same(6);
    visuals.widgets.active.bg_fill = CARD_HI;
    visuals.widgets.active.fg_stroke = Stroke::new(1.0, GOLD);
    visuals.widgets.active.corner_radius = CornerRadius::same(6);
    visuals.slider_trailing_fill = true;
    visuals.window_corner_radius = CornerRadius::same(10);
    ctx.set_visuals(visuals);

    let mut style = (*ctx.style()).clone();
    style.text_styles = [
        (
            TextStyle::Heading,
            FontId::new(22.0, FontFamily::Proportional),
        ),
        (TextStyle::Body, FontId::new(15.0, FontFamily::Proportional)),
        (
            TextStyle::Button,
            FontId::new(15.0, FontFamily::Proportional),
        ),
        (
            TextStyle::Small,
            FontId::new(12.5, FontFamily::Proportional),
        ),
        (
            TextStyle::Monospace,
            FontId::new(13.0, FontFamily::Monospace),
        ),
    ]
    .into();
    style.spacing.item_spacing = egui::vec2(10.0, 8.0);
    style.spacing.button_padding = egui::vec2(14.0, 7.0);
    style.spacing.interact_size.y = 32.0;
    ctx.set_style(style);
}

/// A section label with the gold rule above it.
pub fn section(ui: &mut egui::Ui, title: &str) {
    ui.add_space(6.0);
    let (rect, _resp) =
        ui.allocate_exact_size(egui::vec2(ui.available_width(), 1.0), egui::Sense::hover());
    ui.painter().rect_filled(rect, 0.0, LINE);
    ui.add_space(8.0);
    ui.label(egui::RichText::new(title).color(GOLD).size(13.0));
    ui.add_space(2.0);
}
