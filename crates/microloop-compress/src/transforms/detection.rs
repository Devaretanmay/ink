
use crate::transforms::content_detector::ContentType;
use crate::transforms::magika_detector::magika_detect;
use crate::transforms::unidiff_detector::is_diff;

pub fn detect(content: &str) -> ContentType {
    if content.is_empty() {
        return ContentType::PlainText;
    }

    match magika_detect(content) {
        Ok(ContentType::PlainText) => {
        }
        Ok(content_type) => return content_type,
        Err(e) => {
            tracing::warn!(
                error = %e,
                "magika detection failed; falling through to unidiff tier"
            );
        }
    }

    if is_diff(content) {
        return ContentType::GitDiff;
    }

    ContentType::PlainText
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::transforms::magika_detector::magika_onnx_runtime_supported_by_cpu;

    #[test]
    fn empty_input_short_circuits_to_plain_text() {
        assert_eq!(detect(""), ContentType::PlainText);
    }

    #[test]
    fn json_array_routes_via_tier_1() {
        let payload = r#"[{"id": 1}, {"id": 2}, {"id": 3}]"#;
        if !magika_onnx_runtime_supported_by_cpu() {
            assert_eq!(detect(payload), ContentType::PlainText);
        } else {
            assert_eq!(detect(payload), ContentType::JsonArray);
        }
    }

    #[test]
    fn source_code_routes_via_tier_1() {
        let py = "def hello():\n    print('world')\n\nclass Foo:\n    pass\n";
        if !magika_onnx_runtime_supported_by_cpu() {
            assert_eq!(detect(py), ContentType::PlainText);
        } else {
            assert_eq!(detect(py), ContentType::SourceCode);
        }
    }

    #[test]
    fn html_routes_via_tier_1() {
        let html = "<!DOCTYPE html><html><body><h1>x</h1></body></html>";
        if !magika_onnx_runtime_supported_by_cpu() {
            assert_eq!(detect(html), ContentType::PlainText);
        } else {
            assert_eq!(detect(html), ContentType::Html);
        }
    }

    #[test]
    fn standard_git_diff_routes_via_tier_1_or_2() {
        let diff = "diff --git a/foo.py b/foo.py\n\
                    --- a/foo.py\n\
                    +++ b/foo.py\n\
                    @@ -1,1 +1,2 @@\n \
                    def hello():\n\
                    +    print(\"new\")\n";
        assert_eq!(detect(diff), ContentType::GitDiff);
    }

    #[test]
    fn naked_hunk_diff_routes_via_tier_2() {
        let diff = "--- a/foo.py\n\
                    +++ b/foo.py\n\
                    @@ -1,2 +1,2 @@\n\
                    -old line\n\
                    +new line\n \
                    context line\n";
        assert_eq!(detect(diff), ContentType::GitDiff);
    }

    #[test]
    fn plain_prose_routes_to_plain_text() {
        let prose = "The quick brown fox jumps over the lazy dog. \
                     Just regular English with no special structure.";
        assert_eq!(detect(prose), ContentType::PlainText);
    }

    #[test]
    fn grep_search_results_route_to_plain_text_per_locked_design() {
        let grep = "src/foo.py:42:def process():\n\
                    src/bar.py:10:    return True\n\
                    src/baz.py:7:class Worker:\n";
        let result = detect(grep);
        assert!(
            result == ContentType::PlainText || result == ContentType::SourceCode,
            "grep output should route to PlainText (preferred) or SourceCode (acceptable), got {result:?}"
        );
    }

    #[test]
    fn build_log_output_routes_via_chain() {
        let log = "[INFO] Building target foo\n\
                   [WARN] Deprecated API usage in foo.cpp:45\n\
                   [ERROR] Compilation failed: undefined reference\n";
        let got = detect(log);
        assert!(
            matches!(got, ContentType::PlainText | ContentType::SourceCode),
            "build log should route to PlainText or SourceCode, got {got:?}"
        );
    }

    #[test]
    fn yaml_routes_to_source_code() {
        let yaml = "name: my-app\nversion: 1.0\ndependencies:\n  - foo\n";
        if !magika_onnx_runtime_supported_by_cpu() {
            assert_eq!(detect(yaml), ContentType::PlainText);
        } else {
            assert_eq!(detect(yaml), ContentType::SourceCode);
        }
    }

    #[test]
    fn rust_source_routes_to_source_code() {
        let rs = "use std::collections::HashMap;\n\n\
                  pub struct Counter { counts: HashMap<String, u32> }\n\n\
                  impl Counter {\n    \
                      pub fn new() -> Self { Self { counts: HashMap::new() } }\n\
                  }\n";
        if !magika_onnx_runtime_supported_by_cpu() {
            assert_eq!(detect(rs), ContentType::PlainText);
        } else {
            assert_eq!(detect(rs), ContentType::SourceCode);
        }
    }

    #[test]
    fn chain_is_deterministic_across_repeated_calls() {
        let payload = r#"{"users": [{"id": 1}, {"id": 2}]}"#;
        let a = detect(payload);
        let b = detect(payload);
        let c = detect(payload);
        assert_eq!(a, b);
        assert_eq!(b, c);
    }
}
