//! Update planning and safe installation rules.
//!
//! The Python launcher updates itself from the GitHub releases of Supermedo/uncharted_pc
//! (a zip asset, installed over the port folder while USER_FILES are preserved). The
//! upstream release metadata provides **no signatures and no published asset hashes**, so
//! this module does not pretend to verify any: it does enforce archive safety (no path
//! traversal, no absolute paths, no reparse-point escape) and the preserve-list rules,
//! and it records what a stronger policy would require.

use std::collections::BTreeSet;
use std::path::{Component, Path, PathBuf};

/// Never copied over an installation by an update (same list as USER_FILES in
/// launcher/uncharted_launcher.py).
pub const USER_FILES: &[&str] = &[
    "user",
    "out",
    "art",
    "bbport.ini",
    "input.ini",
    "last_run.log",
];

#[derive(Debug, Clone, PartialEq)]
pub struct Release {
    pub tag: String,
    pub zip_url: String,
    pub page_url: String,
}

/// Pick the windows zip of a GitHub release object (the field the Python updater uses is
/// `browser_download_url` of the asset whose name ends in .zip).
pub fn select_zip(assets: &[(String, String)]) -> Option<(String, String)> {
    assets
        .iter()
        .find(|(name, _)| name.ends_with(".zip"))
        .cloned()
}

/// Version comparison of release tags ("0.3.10" > "0.3.6"): digit runs compared
/// numerically, matching `version_tuple()` in the Python launcher.
pub fn tag_is_newer(candidate: &str, current: &str) -> bool {
    let numbers = |s: &str| -> Vec<u64> {
        let mut out = Vec::new();
        let mut digits = String::new();
        for c in s.chars() {
            if c.is_ascii_digit() {
                digits.push(c);
            } else if !digits.is_empty() {
                out.push(digits.parse().unwrap_or(0));
                digits.clear();
            }
        }
        if !digits.is_empty() {
            out.push(digits.parse().unwrap_or(0));
        }
        out
    };
    numbers(candidate) > numbers(current)
}

/// An archive entry is safe to extract when it stays inside the target directory:
/// no absolute paths, no `..` components, no drive prefixes (C:), no UNC names.
pub fn entry_is_safe(entry: &str) -> bool {
    if entry.is_empty() || entry.starts_with('/') || entry.starts_with('\\') {
        return false;
    }
    if entry.contains(':') {
        return false; // "C:evil" or NTFS alternate data streams
    }
    let path = Path::new(entry);
    path.components().all(|c| match c {
        Component::Normal(_) | Component::CurDir => true,
        Component::ParentDir | Component::RootDir | Component::Prefix(_) => false,
    })
}

/// Paths under the install folder an update must not overwrite.
pub fn is_user_file(relative: &str) -> bool {
    let first = relative
        .split(['/', '\\'])
        .next()
        .unwrap_or_default();
    USER_FILES.contains(&first)
}

/// Plan of files an update writes (everything except the preserved list, entries filtered
/// for safety). Returns (to_write, preserved, rejected_unsafe).
pub fn plan_installation(
    entries: impl IntoIterator<Item = String>,
) -> (Vec<String>, BTreeSet<String>, Vec<String>) {
    let mut write = Vec::new();
    let mut preserved = BTreeSet::new();
    let mut unsafe_entries = Vec::new();
    for entry in entries {
        if !entry_is_safe(&entry) {
            unsafe_entries.push(entry);
        } else if is_user_file(&entry) {
            preserved.insert(entry);
        } else {
            write.push(entry);
        }
    }
    (write, preserved, unsafe_entries)
}

/// Recovery path after a failed update: the staged download lives beside (never inside)
/// the install until `plan_installation` has been validated.
pub fn staging_path(temp_dir: &Path, tag: &str) -> PathBuf {
    temp_dir.join(format!("uncharted-update-{tag}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tag_ordering() {
        assert!(tag_is_newer("0.3.7", "0.3.6"));
        assert!(tag_is_newer("0.3.10", "0.3.6"));
        assert!(tag_is_newer("1.0.0", "0.3.6"));
        assert!(!tag_is_newer("0.3.6", "0.3.6"));
        assert!(!tag_is_newer("0.3.5", "0.3.6"));
        assert!(tag_is_newer("v0.3.7", "0.3.6"), "leading v is part of a digit run");
    }

    #[test]
    fn traversal_and_absolute_entries_are_rejected() {
        assert!(entry_is_safe("Uncharted.exe"));
        assert!(entry_is_safe("bin/bb-probe.exe"));
        assert!(!entry_is_safe("../evil.exe"));
        assert!(!entry_is_safe("a/../../evil.exe"));
        assert!(!entry_is_safe("/etc/passwd"));
        assert!(!entry_is_safe("\\\\server\\share\\x"));
        assert!(!entry_is_safe("C:evil.exe"));
        assert!(!entry_is_safe("folder/..\\..\\evil.exe"));
    }

    #[test]
    fn user_files_survive() {
        let (write, preserved, unsafe_entries) = plan_installation(vec![
            "Uncharted.exe".to_string(),
            "user/SAVEDATA000".to_string(),
            "bbport.ini".to_string(),
            "../escape".to_string(),
            "bin/bb-gpu-capabilities.exe".to_string(),
        ]);
        assert_eq!(write, vec!["Uncharted.exe", "bin/bb-gpu-capabilities.exe"]);
        assert!(preserved.contains("user/SAVEDATA000"));
        assert!(preserved.contains("bbport.ini"));
        assert_eq!(unsafe_entries, vec!["../escape"]);
    }

    #[test]
    fn picks_the_zip_asset() {
        let assets = vec![
            ("uncharted-windows.zip".to_string(), "https://x/zip".to_string()),
            ("Source.code".to_string(), "https://x/src".to_string()),
        ];
        assert_eq!(
            select_zip(&assets),
            Some(("uncharted-windows.zip".to_string(), "https://x/zip".to_string()))
        );
        assert_eq!(select_zip(&[]), None);
    }
}
