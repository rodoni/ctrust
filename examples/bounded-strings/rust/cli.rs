use std::ffi::CStr;

fn decode_hex(text: &str) -> Vec<u8> {
    assert_eq!(text.len() % 2, 0);
    text.as_bytes()
        .chunks_exact(2)
        .map(|chunk| u8::from_str_radix(std::str::from_utf8(chunk).unwrap(), 16).unwrap())
        .collect()
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let capacity: usize = args[1].parse().unwrap();
    assert!(capacity <= 65536);
    let mut dst = vec![0xa5; capacity];
    let initial = decode_hex(&args[2]);
    let count = initial.len().min(capacity);
    dst[..count].copy_from_slice(&initial[..count]);
    let mut source = decode_hex(&args[3]);
    source.push(0);
    let src = CStr::from_bytes_until_nul(&source).unwrap();
    let result = bounded_strings::FUNCTION_CALL(&mut dst, src);
    print!("{result}|");
    for byte in dst { print!("{byte:02x}"); }
    println!();
}
