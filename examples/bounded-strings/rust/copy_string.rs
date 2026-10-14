use std::ffi::CStr;

// ORIGIN_COMMENT
pub fn strlcpy(dst: &mut [u8], src: &CStr) -> usize {
    let bytes = src.to_bytes();
    if !dst.is_empty() {
        let count = bytes.len().min(dst.len() - 1);
        dst[..count].copy_from_slice(&bytes[..count]);
        dst[count] = 0;
    }
    bytes.len()
}
