fn main() {
    let mut state = microloop::MicroloopState::new("max_repeats: 3").unwrap();
    for step in 1..=4 {
        let verdict = microloop::verify(&mut state, b"read_file", br#"{"path":"a.txt"}"#);
        println!("Legacy call-repetition example: step={step}, verdict={verdict}");
    }
}
