
use std::sync::mpsc;
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use magika::Session;
use thiserror::Error;
use tracing;

use crate::transforms::content_detector::ContentType;

pub(crate) fn magika_onnx_runtime_supported_by_cpu() -> bool {
    crate::onnx_cpu::onnx_runtime_supported_by_cpu()
}

#[derive(Debug, Error)]
pub enum MagikaDetectorError {
    #[error("magika session init failed: {0}")]
    Init(String),

    #[error("magika inference failed: {0}")]
    Inference(String),

    #[error("magika session lock poisoned")]
    Poisoned,
}

static MAGIKA_SESSION: OnceLock<Mutex<Result<Session, String>>> = OnceLock::new();

const MAGIKA_INIT_TIMEOUT_SECS_DEFAULT: u64 = 5;

fn magika_init_timeout() -> Duration {
    let secs = std::env::var("HEADROOM_MAGIKA_INIT_TIMEOUT_SECS")
        .ok()
        .and_then(|v| v.trim().parse::<u64>().ok())
        .filter(|&s| s > 0)
        .unwrap_or(MAGIKA_INIT_TIMEOUT_SECS_DEFAULT);
    Duration::from_secs(secs)
}

fn session() -> &'static Mutex<Result<Session, String>> {
    MAGIKA_SESSION.get_or_init(|| {
        if !magika_onnx_runtime_supported_by_cpu() {
            return Mutex::new(Err(
                "Magika ONNX Runtime backend requires AVX2 on this platform; \
                 falling back to non-Magika detection"
                    .to_string(),
            ));
        }

        let timeout = magika_init_timeout();
        let (tx, rx) = mpsc::channel();
        let spawned = std::thread::Builder::new()
            .name("magika-init".into())
            .spawn(move || {
                let _ = tx.send(Session::new().map_err(|e| e.to_string()));
            });
        if let Err(e) = spawned {
            tracing::warn!("magika init thread spawn failed: {e}");
            return Mutex::new(Err(format!("magika init thread spawn failed: {e}")));
        }
        match rx.recv_timeout(timeout) {
            Ok(res) => Mutex::new(res),
            Err(_) => {
                let ort_dylib = std::env::var("ORT_DYLIB_PATH").ok();
                tracing::warn!(
                    timeout_secs = timeout.as_secs(),
                    ort_dylib_path = ort_dylib.as_deref(),
                    "magika ONNX session init timed out; detection falls back to \
                     non-ML tiers for this process. On Windows an unset \
                     ORT_DYLIB_PATH usually means the WinML System32 \
                     onnxruntime.dll was picked up (deadlocks ort init)."
                );
                Mutex::new(Err(format!(
                    "magika session init exceeded {}s timeout; \
                     using non-ML detection tiers",
                    timeout.as_secs()
                )))
            }
        }
    })
}

pub fn magika_detect(content: &str) -> Result<ContentType, MagikaDetectorError> {
    if content.is_empty() {
        return Ok(ContentType::PlainText);
    }

    let mutex = session();
    let mut guard = mutex.lock().map_err(|_| MagikaDetectorError::Poisoned)?;
    let session = guard
        .as_mut()
        .map_err(|e| MagikaDetectorError::Init(e.clone()))?;

    let bytes = content.as_bytes();
    let file_type = session
        .identify_content_sync(bytes)
        .map_err(|e| MagikaDetectorError::Inference(e.to_string()))?;

    Ok(map_magika_label(file_type.info().label))
}

pub fn map_magika_label(label: &str) -> ContentType {
    match label {
        "json" | "jsonl" => ContentType::JsonArray,

        "diff" => ContentType::GitDiff,

        "html" | "xml" => ContentType::Html,

        "rust" | "python" | "javascript" | "typescript" | "go" | "java" | "c" | "cpp" | "cs"
        | "php" | "ruby" | "swift" | "kotlin" | "scala" | "haskell" | "lua" | "dart" | "perl"
        | "shell" | "powershell" | "batch" | "sql" | "css" | "vue" | "groovy" | "clojure"
        | "asm" | "cmake" | "dockerfile" | "makefile" | "yaml" | "toml" | "ini" | "hcl"
        | "jinja" => ContentType::SourceCode,

        "markdown" | "rst" | "latex" | "txt" | "empty" | "unknown" | "undefined" => {
            ContentType::PlainText
        }

        _ => ContentType::PlainText,
    }
}


#[cfg(test)]
mod tests {
    use super::*;

    fn assert_detect(content: &str, expected: ContentType, hint: &str) {
        if !magika_onnx_runtime_supported_by_cpu() {
            match magika_detect(content) {
                Err(MagikaDetectorError::Init(msg)) => {
                    assert!(
                        msg.contains("AVX2"),
                        "{hint}: expected AVX2 error, got: {msg}"
                    );
                }
                other => panic!("{hint}: on no-AVX2 host expected Init(AVX2) error, got {other:?}"),
            }
        } else {
            match magika_detect(content) {
                Ok(got) => {
                    assert_eq!(got, expected, "{hint}: expected {expected:?}, got {got:?}")
                }
                Err(e) => panic!("{hint}: detection failed: {e}"),
            }
        }
    }

    #[test]
    fn empty_input_is_plain_text_without_model_call() {
        let result = magika_detect("").unwrap();
        assert_eq!(result, ContentType::PlainText);
    }

    #[test]
    fn detects_json() {
        assert_detect(
            r#"{"name": "Alice", "age": 30, "tags": ["a", "b"]}"#,
            ContentType::JsonArray,
            "single-object JSON",
        );
    }

    #[test]
    fn detects_json_array() {
        let payload = r#"[{"id": 1, "v": "a"}, {"id": 2, "v": "b"}, {"id": 3, "v": "c"}]"#;
        assert_detect(payload, ContentType::JsonArray, "array-of-records JSON");
    }

    #[test]
    fn detects_python_source() {
        let src = r#"
def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)

class Tree:
    def __init__(self, value):
        self.value = value
        self.children = []
"#;
        assert_detect(src, ContentType::SourceCode, "python class+def");
    }

    #[test]
    fn detects_rust_source() {
        let src = r#"
use std::collections::HashMap;

pub struct Counter {
    counts: HashMap<String, u32>,
}

impl Counter {
    pub fn new() -> Self {
        Self { counts: HashMap::new() }
    }
}
"#;
        assert_detect(src, ContentType::SourceCode, "rust struct+impl");
    }

    #[test]
    fn detects_javascript_source() {
        let src = r#"
const fetchUser = async (id) => {
    const response = await fetch(`/api/users/${id}`);
    if (!response.ok) throw new Error('Not found');
    return response.json();
};
"#;
        assert_detect(src, ContentType::SourceCode, "JS arrow + async");
    }

    #[test]
    fn detects_unified_diff() {
        let diff = r#"diff --git a/foo.py b/foo.py
index abc123..def456 100644
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,4 @@
 def hello():
+    print("new line")
     return "world"
"#;
        assert_detect(diff, ContentType::GitDiff, "git unified diff");
    }

    #[test]
    fn detects_markdown_as_plain_text() {
        let md = "# Hello\n\nThis is **bold** and *italic*.\n\n- Item 1\n- Item 2\n";
        assert_detect(md, ContentType::PlainText, "markdown");
    }

    #[test]
    fn detects_plain_text() {
        let prose = "The quick brown fox jumps over the lazy dog. \
                     This is just regular English prose with no \
                     special structure.";
        assert_detect(prose, ContentType::PlainText, "english prose");
    }

    #[test]
    fn detects_html() {
        let html =
            "<!DOCTYPE html><html><head><title>x</title></head><body><h1>Hi</h1></body></html>";
        assert_detect(html, ContentType::Html, "minimal HTML page");
    }

    #[test]
    fn detects_yaml_as_source_code() {
        let yaml = "name: my-app\nversion: 1.0\ndependencies:\n  - foo\n  - bar\n";
        assert_detect(yaml, ContentType::SourceCode, "YAML config");
    }

    #[test]
    fn detects_shell_script_as_source_code() {
        let sh = "#!/bin/bash\nset -euo pipefail\nfor f in *.txt; do\n  echo \"$f\"\ndone\n";
        assert_detect(sh, ContentType::SourceCode, "bash script with shebang");
    }

    #[test]
    fn detects_sql_as_source_code() {
        let sql = "SELECT u.id, u.name, COUNT(o.id) AS order_count \
                   FROM users u LEFT JOIN orders o ON u.id = o.user_id \
                   WHERE u.active = TRUE GROUP BY u.id, u.name;";
        assert_detect(sql, ContentType::SourceCode, "SQL query");
    }

    #[test]
    fn singleton_session_is_reused_across_calls() {
        if !magika_onnx_runtime_supported_by_cpu() {
            let r1 = magika_detect("hello world");
            let r2 = magika_detect("def f(): pass");
            let r3 = magika_detect(r#"{"a":1}"#);
            for r in [&r1, &r2, &r3] {
                match r {
                    Err(MagikaDetectorError::Init(msg)) => {
                        assert!(msg.contains("AVX2"), "expected AVX2 error, got: {msg}");
                    }
                    other => panic!("on no-AVX2 host expected Init(AVX2) error, got {other:?}"),
                }
            }
        } else {
            magika_detect("hello world").unwrap();
            magika_detect("def f(): pass").unwrap();
            magika_detect(r#"{"a":1}"#).unwrap();
        }
    }

    #[test]
    fn unmapped_labels_route_to_plain_text() {
        assert_eq!(map_magika_label("ace"), ContentType::PlainText);
        assert_eq!(map_magika_label("flac"), ContentType::PlainText);
        assert_eq!(map_magika_label("3gp"), ContentType::PlainText);
        assert_eq!(
            map_magika_label("garbage_unseen_label"),
            ContentType::PlainText
        );
    }

    #[test]
    fn known_label_table_round_trips() {
        assert_eq!(map_magika_label("json"), ContentType::JsonArray);
        assert_eq!(map_magika_label("jsonl"), ContentType::JsonArray);
        assert_eq!(map_magika_label("diff"), ContentType::GitDiff);
        assert_eq!(map_magika_label("html"), ContentType::Html);
        assert_eq!(map_magika_label("rust"), ContentType::SourceCode);
        assert_eq!(map_magika_label("python"), ContentType::SourceCode);
        assert_eq!(map_magika_label("yaml"), ContentType::SourceCode);
        assert_eq!(map_magika_label("markdown"), ContentType::PlainText);
        assert_eq!(map_magika_label("txt"), ContentType::PlainText);
        assert_eq!(map_magika_label("empty"), ContentType::PlainText);
    }
}
