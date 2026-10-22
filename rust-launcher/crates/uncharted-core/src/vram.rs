//! VRAM guidance from bb-gpu-capabilities --memory, mirroring launcher/uncharted_hw.py.
//! The README documents that 4 GB cards "run out of video memory and crash after a while"
//! and says to keep Anti-aliasing and Frame generation off there. The launcher surfaces
//! that advice and never changes settings by itself.

/// The README's documented recommendation for Anti-aliasing and Frame generation.
pub const RECOMMENDED_VRAM_MB: u64 = 6 * 1024;

/// (name, MiB) from the "GPU: name, N MiB of video memory" line, else (None, None).
/// The greedy name keeps commas in device names ("Contoso Turbo, Turbo Edition") intact.
pub fn parse_gpu_memory(stderr: &str) -> (Option<String>, Option<u64>) {
    for line in stderr.lines() {
        if let Some(rest) = line.strip_prefix("GPU: ") {
            if let Some((name, tail)) = rest.rsplit_once(", ") {
                if let Some(mib) = tail.strip_suffix(" MiB of video memory") {
                    if let Ok(value) = mib.parse::<u64>() {
                        return (Some(name.to_string()), Some(value));
                    }
                }
            }
        }
    }
    (None, None)
}

/// The README's advice when a small card runs Anti-aliasing or Frame generation, else None.
/// A visible hint only: settings stay exactly as the user left them.
pub fn low_vram_warning(
    vram_mb: Option<u64>,
    anti_aliasing: &str,
    frame_generation: &str,
) -> Option<String> {
    let vram = vram_mb?;
    if vram >= RECOMMENDED_VRAM_MB {
        return None;
    }
    if anti_aliasing == "off" && frame_generation == "off" {
        return None;
    }
    Some(format!(
        "This card reports {vram} MiB of video memory. Anti-aliasing and frame generation \
         can run it out of memory and crash the games (README); keep them off on cards \
         with less than 6 GB."
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_name_and_mib() {
        let (name, mib) =
            parse_gpu_memory("GPU: NVIDIA GeForce RTX 2050/PCIe/SSE2, 4096 MiB of video memory\n");
        assert_eq!(name.as_deref(), Some("NVIDIA GeForce RTX 2050/PCIe/SSE2"));
        assert_eq!(mib, Some(4096));
    }

    #[test]
    fn commas_in_the_device_name_stay_in_the_name() {
        let (name, mib) =
            parse_gpu_memory("GPU: Contoso Turbo, Turbo Edition, 8192 MiB of video memory\n");
        assert_eq!(name.as_deref(), Some("Contoso Turbo, Turbo Edition"));
        assert_eq!(mib, Some(8192));
    }

    #[test]
    fn other_modes_do_not_parse() {
        assert_eq!(
            parse_gpu_memory(
                "GPU: NVIDIA GeForce RTX 2050, discrete, 4096 MiB: live resolution changes on\n"
            ),
            (None, None)
        );
    }

    #[test]
    fn warning_rules() {
        assert!(low_vram_warning(Some(4096), "taa", "off").is_some());
        assert!(low_vram_warning(Some(4096), "off", "fsr3").is_some());
        assert!(low_vram_warning(Some(4096), "off", "off").is_none());
        assert!(low_vram_warning(Some(RECOMMENDED_VRAM_MB), "taa", "fsr3").is_none());
        assert!(low_vram_warning(Some(8192), "taa", "fsr3").is_none());
        assert!(low_vram_warning(None, "taa", "fsr3").is_none());
    }
}
