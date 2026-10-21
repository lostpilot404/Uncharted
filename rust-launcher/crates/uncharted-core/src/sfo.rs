//! Minimal PlayStation SFO reader for `sce_sys/param.sfo`, mirroring `sfo()` in
//! scripts/prepare.py: magic "\0PSF", count entries of `<HHIII` (key offset, format,
//! size, capacity, data offset), keys NUL-terminated in the key table, values in the
//! data table (0x404 = u32, anything else = UTF-8 string).

use std::collections::BTreeMap;

#[derive(Debug, Clone, PartialEq)]
pub enum SfoValue {
    U32(u32),
    Str(String),
}

pub fn parse_sfo(data: &[u8]) -> Result<BTreeMap<String, SfoValue>, String> {
    if data.len() < 20 {
        return Err("SFO too small".into());
    }
    if &data[0..4] != b"\0PSF" {
        return Err("bad SFO signature".into());
    }
    let u32_at = |pos: usize| -> u32 {
        u32::from_le_bytes([data[pos], data[pos + 1], data[pos + 2], data[pos + 3]])
    };
    let u16_at = |pos: usize| -> u16 { u16::from_le_bytes([data[pos], data[pos + 1]]) };
    let keys = u32_at(8) as usize;
    let values = u32_at(12) as usize;
    let count = u32_at(16) as usize;
    let mut result = BTreeMap::new();
    for i in 0..count {
        let entry = 20 + i * 16;
        if entry + 16 > data.len() {
            return Err(format!("SFO entry {i} out of bounds"));
        }
        let key_off = u16_at(entry) as usize;
        let fmt = u16_at(entry + 2);
        let size = u32_at(entry + 4) as usize;
        let data_off = u32_at(entry + 12) as usize;
        let key_start = keys + key_off;
        let key_end = data[key_start..]
            .iter()
            .position(|&b| b == 0)
            .map(|p| key_start + p)
            .ok_or_else(|| format!("SFO key {i} not terminated"))?;
        let key = String::from_utf8_lossy(&data[key_start..key_end]).into_owned();
        let val_start = values + data_off;
        let val_end = val_start + size;
        if val_end > data.len() {
            return Err(format!("SFO value for '{key}' out of bounds"));
        }
        let raw = &data[val_start..val_end];
        let value = if fmt == 0x404 {
            if raw.len() < 4 {
                return Err(format!("SFO u32 for '{key}' too small"));
            }
            SfoValue::U32(u32::from_le_bytes([raw[0], raw[1], raw[2], raw[3]]))
        } else {
            let end = raw.iter().position(|&b| b == 0).unwrap_or(raw.len());
            SfoValue::Str(String::from_utf8_lossy(&raw[..end]).into_owned())
        };
        result.insert(key, value);
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Builds an SFO the way the tooling on the PC side does.
    fn fixture() -> Vec<u8> {
        let title = "UNCHARTED™ 2\0";
        let title_id = "CUSA02320\0";
        let app_ver = "01.02\0";
        let mut keys = Vec::new();
        let mut values = Vec::new();
        let mut entries = Vec::new();
        let param_u32 = 7u32.to_le_bytes();
        for (key, fmt, val) in [
            ("APP_VER", 0x204u16, app_ver.as_bytes()),
            ("TITLE", 0x204, title.as_bytes()),
            ("TITLE_ID", 0x204, title_id.as_bytes()),
            ("USER_DEFINED_PARAM_1", 0x404, param_u32.as_slice()),
        ] {
            entries.push((
                keys.len() as u16,
                fmt,
                val.len() as u32,
                values.len() as u32,
            ));
            keys.extend_from_slice(key.as_bytes());
            keys.push(0);
            values.extend_from_slice(val);
        }
        let key_table = 20 + entries.len() * 16;
        let data_table = key_table + keys.len();
        let mut out = Vec::new();
        out.extend_from_slice(b"\0PSF");
        out.extend_from_slice(&1u32.to_le_bytes());
        out.extend_from_slice(&(key_table as u32).to_le_bytes());
        out.extend_from_slice(&(data_table as u32).to_le_bytes());
        out.extend_from_slice(&(entries.len() as u32).to_le_bytes());
        for (key_off, fmt, size, val_off) in entries {
            out.extend_from_slice(&key_off.to_le_bytes());
            out.extend_from_slice(&fmt.to_le_bytes());
            out.extend_from_slice(&size.to_le_bytes());
            out.extend_from_slice(&size.to_le_bytes());
            out.extend_from_slice(&val_off.to_le_bytes());
        }
        out.extend_from_slice(&keys);
        out.extend_from_slice(&values);
        out
    }

    #[test]
    fn reads_the_fields_the_launcher_shows() {
        let sfo = parse_sfo(&fixture()).unwrap();
        assert_eq!(
            sfo.get("TITLE_ID"),
            Some(&SfoValue::Str("CUSA02320".into())),
            "{sfo:?}"
        );
        assert_eq!(sfo.get("APP_VER"), Some(&SfoValue::Str("01.02".into())));
        assert_eq!(sfo.get("USER_DEFINED_PARAM_1"), Some(&SfoValue::U32(7)));
    }

    #[test]
    fn rejects_a_wrong_signature() {
        assert!(parse_sfo(b"NOPE more than twenty bytes................").is_err());
    }

    #[test]
    fn rejects_truncated_data() {
        let mut data = fixture();
        data.truncate(data.len() - 3);
        assert!(parse_sfo(&data).is_err());
    }
}
