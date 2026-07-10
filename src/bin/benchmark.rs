use std::hint::black_box;
use std::time::{Duration, Instant};

use microloop::history::HistoryTracker;
use microloop::verify;
use microloop::MicroloopState;
use microloop_compress::route_and_compress;

const YAML_CONFIG: &str = r#"
default:
  trajectory_gate:
    max_repeats: 3
    count_mode: errors_only
    volatile_fields: ["line"]

tools:
  - name: delete_line
    trajectory_gate:
      max_repeats: 3
      count_mode: errors_only
      volatile_fields: []
  - name: read_file
    trajectory_gate:
      max_repeats: 5
      cost_tier: low
  - name: write_file
    trajectory_gate:
      max_repeats: 5
      cost_tier: medium
  - name: search_web
    trajectory_gate:
      max_repeats: 10
      cost_tier: high
      cost_weight: 2.5
  - name: execute_bash
    trajectory_gate:
      max_repeats: 3
      cost_tier: high
      cost_weight: 5.0
"#;

// ~800 bytes, triggers compression (threshold is 512)
const JSON_ARRAY_PAYLOAD: &str = r#"[
  {"id": 1, "name": "Alice", "role": "engineer", "location": "NYC", "projects": ["foo", "bar"]},
  {"id": 2, "name": "Bob", "role": "designer", "location": "SF", "projects": ["baz"]},
  {"id": 3, "name": "Carol", "role": "pm", "location": "London", "projects": ["foo", "baz", "qux"]},
  {"id": 4, "name": "Dave", "role": "engineer", "location": "Berlin", "projects": ["bar"]},
  {"id": 5, "name": "Eve", "role": "designer", "location": "Tokyo", "projects": ["qux"]},
  {"id": 6, "name": "Frank", "role": "engineer", "location": "NYC", "projects": ["foo", "bar"]},
  {"id": 7, "name": "Grace", "role": "pm", "location": "SF", "projects": ["baz", "qux"]}
]"#;

// ~900 bytes
const GIT_DIFF_PAYLOAD: &str = r#"diff --git a/src/core.rs b/src/core.rs
--- a/src/core.rs
+++ b/src/core.rs
@@ -10,6 +10,8 @@
 pub fn process(items: &[Item]) -> Result<()> {
     for item in items {
+        // validate before processing
+        item.validate()?;
         let result = transform(item)?;
         store(result)?;
     }
@@ -45,8 +47,10 @@
 fn cleanup() {
-    db.execute("DELETE FROM temp")?;
-    db.execute("VACUUM")?;
+    db.execute("DELETE FROM temp WHERE age < 30")?;
+    db.execute("VACUUM FULL")?;
+    db.execute("ANALYZE")?;
     Ok(())
 }

@@ -120,8 +124,12 @@
 pub struct Config {
     pub host: String,
     pub port: u16,
+    pub timeout: Duration,
+    pub retries: u32,
+    pub backoff: f64,
 }

"#;

// ~700 bytes
const BUILD_OUTPUT_PAYLOAD: &str = r#"[INFO] Compiling microloop v0.2.0
[INFO] Compiling microloop-compress v0.1.0
[WARN] unused import: `std::collections::HashMap` in src/history.rs
[ERROR] failed to run custom build command for `onnxruntime-sys v0.0.20`
  Caused by: OnnxRuntime build failed
  Caused by: CMake was not found in PATH
  Please install cmake: brew install cmake
[INFO] Running `rustfmt` on 42 files
[INFO] Formatted 42 files in 1.2s
[ERROR] compilation error in src/transforms/detection.rs: missing match arm
[WARN] deprecated method `old_api()` used in 3 locations
  --> src/proxy.rs:150
  --> src/cache.rs:42
  --> src/ccr.rs:88
[INFO] Build completed in 8.42s (with 2 errors, 3 warnings)
"#;

// ~750 bytes
const SEARCH_RESULTS_PAYLOAD: &str = r#"src/core.rs:42:    pub fn validate(&self) -> bool {
src/core.rs:85:    pub fn process(&mut self, input: &str) -> Result<String> {
src/core.rs:120:    fn cleanup(&self) -> Result<()> {
src/history.rs:15:    pub fn check_loop(&mut self, tool: &str, args: &str) -> Result<()> {
src/history.rs:42:    fn detect_oscillation(&self) -> Option<usize> {
src/history.rs:85:    fn check_argument_variance(&self, period: usize) -> bool {
src/engine.rs:22:    pub fn validate(&self, args: &str) -> Result<()> {
src/engine.rs:55:    fn compile_rule(cfg: &RuleConfig) -> Result<CompiledRule, String> {
src/config.rs:18:    fn from_yaml(yaml: &str) -> Result<Self, String> {
src/state.rs:42:    fn get_effective_threshold(&self, tool: &str) -> usize {
Cargo.toml:5:  edition = "2024"
README.md:12: documentation.md:35:
"#;

// ~600 bytes
const SOURCE_CODE_PAYLOAD: &str = r#"import asyncio
from typing import Optional, List

class Service:
    def __init__(self, name: str, timeout: float = 30.0):
        self.name = name
        self.timeout = timeout
        self._connections: List[str] = []

    async def connect(self, url: str) -> bool:
        try:
            conn = await asyncio.wait_for(
                self._open(url), timeout=self.timeout
            )
            self._connections.append(url)
            return True
        except asyncio.TimeoutError:
            return False

    async def broadcast(self, message: str) -> int:
        sent = 0
        for conn in self._connections:
            await self._send(conn, message)
            sent += 1
        return sent

    async def _open(self, url: str) -> str:
        await asyncio.sleep(0.1)
        return f"conn://{url}"

    async def _send(self, conn: str, msg: str) -> None:
        await asyncio.sleep(0.05)
"#;

struct BenchResult {
    label: &'static str,
    total: Duration,
    iters: usize,
    per_op: Duration,
}

fn bench<F: FnMut()>(label: &'static str, iters: usize, mut f: F) -> BenchResult {
    let start = Instant::now();
    for _ in 0..iters {
        f();
        black_box(());
    }
    let total = start.elapsed();
    BenchResult {
        label,
        total,
        iters,
        per_op: total / iters as u32,
    }
}

fn main() {
    let mut results: Vec<BenchResult> = Vec::new();

    // -------------------------------------------------------
    // 1. App initialization
    // -------------------------------------------------------
    results.push(bench("state_init", 10_000, || {
        let _state = MicroloopState::new(YAML_CONFIG).unwrap();
    }));

    // -------------------------------------------------------
    // 2. Cold start — first verify on a fresh state
    // -------------------------------------------------------
    results.push(bench("cold_start_verify", 10_000, || {
        let mut state = MicroloopState::new(YAML_CONFIG).unwrap();
        let _ = verify(
            black_box(&mut state),
            black_box(b"read_file"),
            black_box(b"{\"path\": \"src/main.rs\"}"),
        );
    }));

    // -------------------------------------------------------
    // 3. Compression pipeline — each content type
    // -------------------------------------------------------
    results.push(bench("compress_json_array", 50_000, || {
        let _ = route_and_compress(black_box(JSON_ARRAY_PAYLOAD));
    }));

    results.push(bench("compress_git_diff", 50_000, || {
        let _ = route_and_compress(black_box(GIT_DIFF_PAYLOAD));
    }));

    results.push(bench("compress_build_output", 50_000, || {
        let _ = route_and_compress(black_box(BUILD_OUTPUT_PAYLOAD));
    }));

    results.push(bench("compress_search_results", 50_000, || {
        let _ = route_and_compress(black_box(SEARCH_RESULTS_PAYLOAD));
    }));

    results.push(bench("compress_source_code", 50_000, || {
        let _ = route_and_compress(black_box(SOURCE_CODE_PAYLOAD));
    }));

    // -------------------------------------------------------
    // 4. Tool verification end-to-end
    // -------------------------------------------------------
    {
        let mut state = MicroloopState::new(YAML_CONFIG).unwrap();
        let tool = b"read_file";
        let args = b"{\"path\": \"src/main.rs\"}";

        results.push(bench("verify_e2e", 100_000, || {
            let _ = verify(
                black_box(&mut state),
                black_box(tool),
                black_box(args),
            );
        }));
    }

    // -------------------------------------------------------
    // 5. Oscillation detection throughput
    // -------------------------------------------------------
    {
        let mut tracker = HistoryTracker::new();
        let tools = [
            ("search_web", "{\"q\": \"rust async\"}"),
            ("read_file", "{\"path\": \"src/main.rs\"}"),
            ("search_web", "{\"q\": \"rust traits\"}"),
            ("read_file", "{\"path\": \"src/lib.rs\"}"),
            ("search_web", "{\"q\": \"rust macros\"}"),
            ("read_file", "{\"path\": \"src/history.rs\"}"),
        ];

        results.push(bench("oscillation_detect", 500_000, || {
            for &(t, a) in &tools {
                let _ = tracker.check_loop(t, a, false, 10, Some(20));
            }
        }));
    }

    // -------------------------------------------------------
    // 6. Mixed workload — realistic LLM conversation
    // -------------------------------------------------------
    {
        let mut state = MicroloopState::new(YAML_CONFIG).unwrap();
        let payloads = [
            ("read_file", "{\"path\": \"src/main.rs\"}", SOURCE_CODE_PAYLOAD),
            ("search_web", "{\"q\": \"rust error handling\"}", SEARCH_RESULTS_PAYLOAD),
            ("read_file", "{\"path\": \"Cargo.toml\"}", SOURCE_CODE_PAYLOAD),
            ("execute_bash", "{\"cmd\": \"cargo check\"}", BUILD_OUTPUT_PAYLOAD),
            ("read_file", "{\"path\": \"src/core.rs\"}", SOURCE_CODE_PAYLOAD),
            ("write_file", "{\"path\": \"src/main.rs\", \"content\": \"...\"}", SOURCE_CODE_PAYLOAD),
            ("search_web", "{\"q\": \"rust async patterns\"}", SEARCH_RESULTS_PAYLOAD),
            ("execute_bash", "{\"cmd\": \"cargo build\"}", BUILD_OUTPUT_PAYLOAD),
        ];

        results.push(bench("mixed_workload", 10_000, || {
            for (tool, args, content) in &payloads {
                let _ = verify(black_box(&mut state), tool.as_bytes(), args.as_bytes());
                let _ = route_and_compress(black_box(content));
            }
        }));
    }

    // -------------------------------------------------------
    // 7. Short input bypass (sub-512 chars) — fast path
    // -------------------------------------------------------
    let short_payload = r#"{"id": 1, "name": "test"}"#;
    results.push(bench("compress_short_fastpath", 200_000, || {
        let _ = route_and_compress(black_box(short_payload));
    }));

    // -------------------------------------------------------
    // Print results
    // -------------------------------------------------------
    println!();
    println!("{:40} {:>12} {:>14} {:>14} {:>10}", "Benchmark", "Iterations", "Total Time", "Per Op", "Ops/s");
    println!("{}", "-".repeat(94));

    for r in &results {
        let ops = r.iters as f64 / r.total.as_secs_f64();
        println!(
            "{:40} {:>12} {:>8.3?} {:>6.1} ns  {:>10.0}",
            r.label,
            r.iters,
            r.total,
            r.per_op.as_nanos(),
            ops,
        );
    }

    println!();
    let total_time: Duration = results.iter().map(|r| r.total).sum();
    println!("Total benchmark time: {:?}", total_time);
}
