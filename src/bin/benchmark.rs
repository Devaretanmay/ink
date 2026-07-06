use microloop::history::HistoryTracker;
use std::time::Instant;

fn main() {
    let mut tracker = HistoryTracker::new();
    let iters = 1_000_000;
    
    let tools = vec![
        ("search_web", "{\"query\": \"rust async\"}"),
        ("read_file", "{\"path\": \"src/main.rs\"}"),
        ("search_web", "{\"query\": \"rust loop\"}"),
        ("read_file", "{\"path\": \"src/lib.rs\"}"),
    ];

    let start = Instant::now();
    for i in 0..iters {
        let (tool, args) = tools[i % tools.len()];
        let _ = tracker.check_loop(tool, args, false, 10, Some(20));
    }
    let elapsed = start.elapsed();
    
    println!("Processed {} tool calls in {:?}", iters, elapsed);
    println!("Time per call: {:?}", elapsed / iters as u32);
}
