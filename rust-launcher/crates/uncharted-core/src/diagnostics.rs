//! Diagnostics report with redaction: the launcher can copy/export a report that is safe
//! to attach to a bug report. Redaction covers Windows user-profile paths, the player name
//! and absolute game paths (they reveal folder layout but not the game's contents).

/// Replace the user-profile directory with `%USERPROFILE%` and the given player name with
/// `[player]`, in that order. Case-insensitive on the Windows forms.
pub fn redact(text: &str, user_profile: &str, player_name: &str) -> String {
    let mut out = text.to_string();
    if !user_profile.is_empty() {
        out = replace_ignore_ascii_case(&out, user_profile, "%USERPROFILE%");
    }
    if !player_name.trim().is_empty() {
        out = replace_ignore_ascii_case(&out, player_name.trim(), "[player]");
    }
    out
}

/// Build the report body: launcher version, settings (redacted), detected games, GPU line,
/// and the tail of the log. Never includes file contents or checksums of game data.
pub fn report(
    launcher_version: &str,
    settings_json: &str,
    gpu_line: &str,
    log_tail: &str,
    user_profile: &str,
    player_name: &str,
) -> String {
    let body = format!(
        "Uncharted launcher diagnostics\n\
         launcher: Rust prototype {launcher_version}\n\
         gpu: {gpu_line}\n\
         --- settings (redacted) ---\n{settings_json}\n\
         --- log tail (redacted) ---\n{log_tail}\n"
    );
    redact(&body, user_profile, player_name)
}

fn replace_ignore_ascii_case(haystack: &str, needle: &str, replacement: &str) -> String {
    if needle.is_empty() {
        return haystack.to_string();
    }
    let lower_h = haystack.to_ascii_lowercase();
    let lower_n = needle.to_ascii_lowercase();
    let mut out = String::with_capacity(haystack.len());
    let mut at = 0;
    while let Some(found) = lower_h[at..].find(&lower_n) {
        let pos = at + found;
        out.push_str(&haystack[at..pos]);
        out.push_str(replacement);
        at = pos + needle.len();
    }
    out.push_str(&haystack[at..]);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacts_profile_path_anywhere() {
        // The name appears both as the player label and inside the profile path: the
        // path is redacted first (consuming the embedded copy), the label becomes
        // [player].
        let log = "Player Алиса loaded C:\\Users\\Алиса\\Uncharted\\user\\save.dat";
        let out = redact(log, "C:\\Users\\Алиса", "Алиса");
        assert!(!out.contains("Алиса"), "{out}");
        assert!(out.contains("%USERPROFILE%"));
        assert!(out.contains("Player [player] loaded"), "{out}");
    }

    #[test]
    fn empty_inputs_leave_the_text_alone() {
        assert_eq!(redact("abc", "", ""), "abc");
        assert_eq!(redact("abc", "", "   "), "abc");
    }

    #[test]
    fn report_contains_sections() {
        let out = report("0.1.0", "{}", "GPU: none", "hello", "C:\\Users\\x", "");
        assert!(out.contains("settings (redacted)"));
        assert!(out.contains("log tail (redacted)"));
        assert!(out.contains("hello"));
    }
}
