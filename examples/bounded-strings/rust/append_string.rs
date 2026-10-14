use std::ffi::CStr;

// ORIGIN_COMMENT
pub fn strlcat(dst: &mut [u8], src: &CStr) -> usize {
    let bytes = src.to_bytes();
    let initial_len = dst.iter().position(|&byte| byte == 0).unwrap_or(dst.len());
    if initial_len < dst.len() {
        let count = bytes.len().min(dst.len() - initial_len - 1);
        dst[initial_len..initial_len + count].copy_from_slice(&bytes[..count]);
        dst[initial_len + count] = 0;
    }
    initial_len + bytes.len()
}
