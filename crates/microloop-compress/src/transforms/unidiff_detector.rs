
use crate::transforms::content_detector::ContentType;
use unidiff::PatchSet;

pub fn is_diff(content: &str) -> bool {
    if content.is_empty() {
        return false;
    }

    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let mut patch = PatchSet::new();
        if patch.parse(content).is_err() {
            return false;
        }

        !patch.is_empty() && patch.files().iter().any(|f| !f.is_empty())
    }))
    .unwrap_or(false)
}

pub fn detect_diff(content: &str) -> Option<ContentType> {
    if is_diff(content) {
        Some(ContentType::GitDiff)
    } else {
        None
    }
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_input_is_not_a_diff() {
        assert!(!is_diff(""));
        assert_eq!(detect_diff(""), None);
    }

    #[test]
    fn plain_prose_is_not_a_diff() {
        let prose = "The quick brown fox jumps over the lazy dog. \
                     This is just regular English prose.";
        assert!(!is_diff(prose));
    }

    #[test]
    fn json_is_not_a_diff() {
        let json = r#"{"name": "Alice", "tags": ["a", "b", "c"]}"#;
        assert!(!is_diff(json));
    }

    #[test]
    fn source_code_is_not_a_diff() {
        let py = "def foo():\n    return 42\n\nclass Bar:\n    pass\n";
        assert!(!is_diff(py));
    }

    #[test]
    fn standard_git_diff_detected() {
        let diff = "diff --git a/foo.py b/foo.py\n\
                    index abc123..def456 100644\n\
                    --- a/foo.py\n\
                    +++ b/foo.py\n\
                    @@ -1,3 +1,4 @@\n \
                    def hello():\n\
                    +    print(\"new\")\n     \
                    return \"world\"\n\
                    -    # gone\n";
        assert!(is_diff(diff));
        assert_eq!(detect_diff(diff), Some(ContentType::GitDiff));
    }

    #[test]
    fn naked_hunk_without_git_header_detected() {
        let diff = "--- a/foo.py\n\
                    +++ b/foo.py\n\
                    @@ -1,2 +1,2 @@\n\
                    -old line\n\
                    +new line\n \
                    context\n";
        assert!(is_diff(diff));
    }

    #[test]
    fn multi_file_diff_detected() {
        let diff = "--- a/foo.py\n\
                    +++ b/foo.py\n\
                    @@ -1,1 +1,1 @@\n\
                    -old\n\
                    +new\n\
                    --- a/bar.py\n\
                    +++ b/bar.py\n\
                    @@ -1,1 +1,1 @@\n\
                    -gone\n\
                    +here\n";
        assert!(is_diff(diff));
    }

    #[test]
    fn empty_patch_set_is_not_a_diff() {
        let almost = "Some prose mentioning @@ in passing.\n\
                      And maybe even --- a sentence with dashes.\n";
        assert!(!is_diff(almost));
    }

    #[test]
    fn truncated_diff_treated_consistently() {
        let truncated = "--- a/foo.py\n\
                         +++ b/foo.py\n\
                         @@ -1,1 +1,";
        let _ = is_diff(truncated);
    }

    #[test]
    fn diff_with_added_file_only() {
        let diff = "diff --git a/new.py b/new.py\n\
                    new file mode 100644\n\
                    index 0000000..9b710f3\n\
                    --- /dev/null\n\
                    +++ b/new.py\n\
                    @@ -0,0 +1,3 @@\n\
                    +line one\n\
                    +line two\n\
                    +line three\n";
        assert!(is_diff(diff));
    }

    #[test]
    fn diff_with_removed_file_only() {
        let diff = "diff --git a/gone.py b/gone.py\n\
                    deleted file mode 100644\n\
                    index 9b710f3..0000000\n\
                    --- a/gone.py\n\
                    +++ /dev/null\n\
                    @@ -1,2 +0,0 @@\n\
                    -line one\n\
                    -line two\n";
        assert!(is_diff(diff));
    }

    #[test]
    fn html_is_not_a_diff() {
        let html = "<!DOCTYPE html><html><body><h1>Hi</h1></body></html>";
        assert!(!is_diff(html));
    }

    #[test]
    fn yaml_is_not_a_diff() {
        let yaml = "name: my-app\nversion: 1.0\ndependencies:\n  - foo\n";
        assert!(!is_diff(yaml));
    }

    #[test]
    fn detect_diff_returns_none_on_negative() {
        assert_eq!(detect_diff("not a diff"), None);
        assert_eq!(detect_diff("{}"), None);
        assert_eq!(detect_diff(""), None);
    }

    #[test]
    fn orphaned_target_line_does_not_panic() {
        assert!(!is_diff("+++ x"));
        assert_eq!(detect_diff("+++ x"), None);
        assert!(!is_diff("some prose\n+++ target without a source\nmore"));
    }
}
