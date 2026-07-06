
use std::collections::HashSet;
use std::sync::OnceLock;

const HTML5_TAGS: &[&str] = &[
    "html",
    "base",
    "head",
    "link",
    "meta",
    "style",
    "title",
    "body",
    "address",
    "article",
    "aside",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hgroup",
    "main",
    "nav",
    "section",
    "search",
    "blockquote",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "hr",
    "li",
    "menu",
    "ol",
    "p",
    "pre",
    "ul",
    "a",
    "abbr",
    "b",
    "bdi",
    "bdo",
    "br",
    "cite",
    "code",
    "data",
    "dfn",
    "em",
    "i",
    "kbd",
    "mark",
    "q",
    "rp",
    "rt",
    "ruby",
    "s",
    "samp",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "time",
    "u",
    "var",
    "wbr",
    "area",
    "audio",
    "img",
    "map",
    "track",
    "video",
    "embed",
    "iframe",
    "object",
    "param",
    "picture",
    "portal",
    "source",
    "svg",
    "math",
    "canvas",
    "noscript",
    "script",
    "del",
    "ins",
    "caption",
    "col",
    "colgroup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "button",
    "datalist",
    "fieldset",
    "form",
    "input",
    "label",
    "legend",
    "meter",
    "optgroup",
    "option",
    "output",
    "progress",
    "select",
    "textarea",
    "details",
    "dialog",
    "summary",
    "slot",
    "template",
];

fn known_html_tags() -> &'static HashSet<&'static str> {
    static SET: OnceLock<HashSet<&'static str>> = OnceLock::new();
    SET.get_or_init(|| HTML5_TAGS.iter().copied().collect())
}

const DEFAULT_PREFIX: &str = "{{HEADROOM_TAG_";
const PLACEHOLDER_SUFFIX: &str = "}}";

#[derive(Debug, Default, Clone)]
pub struct ProtectStats {
    pub tags_seen: usize,
    pub html_tags_skipped: usize,
    pub custom_blocks_protected: usize,
    pub self_closing_protected: usize,
    pub orphan_closes: usize,
    pub placeholder_collision_avoided: bool,
}

pub fn is_known_html_tag(tag_name: &str) -> bool {
    let set = known_html_tags();
    if set.contains(tag_name) {
        return true;
    }
    if tag_name.bytes().any(|b| b.is_ascii_uppercase()) {
        let lower = tag_name.to_ascii_lowercase();
        return set.contains(lower.as_str());
    }
    false
}

pub fn known_html_tag_names() -> &'static [&'static str] {
    HTML5_TAGS
}

fn pick_placeholder_prefix(text: &str) -> (String, bool) {
    if !text.contains(DEFAULT_PREFIX) {
        return (DEFAULT_PREFIX.to_string(), false);
    }
    for salt in 0u32..16 {
        let candidate = format!("{{{{HEADROOM_TAG_{salt}_");
        if !text.contains(&candidate) {
            return (candidate, true);
        }
    }
    static FALLBACK: OnceLock<String> = OnceLock::new();
    let prefix = FALLBACK
        .get_or_init(|| "{{HEADROOM_TAG_FALLBACK_a4f1c7e2_".to_string())
        .clone();
    (prefix, true)
}

#[derive(Debug)]
struct OpenTag {
    name_lower: String,
    open_start: usize,
}

enum TagParse {
    Open {
        name_end: usize,
        tag_end: usize,
        is_self_closing: bool,
    },
    Close { name_end: usize, tag_end: usize },
    NotTag,
}

fn parse_tag_at(bytes: &[u8], start: usize) -> TagParse {
    debug_assert!(bytes[start] == b'<');
    let mut i = start + 1;
    let n = bytes.len();
    if i >= n {
        return TagParse::NotTag;
    }

    let is_close = bytes[i] == b'/';
    if is_close {
        i += 1;
    }
    if i >= n {
        return TagParse::NotTag;
    }
    let name_start = i;
    if !is_name_start(bytes[i]) {
        return TagParse::NotTag;
    }
    i += 1;
    while i < n && is_name_cont(bytes[i]) {
        i += 1;
    }
    let name_end = i;
    if name_end == name_start {
        return TagParse::NotTag;
    }

    if is_close {
        while i < n && bytes[i].is_ascii_whitespace() {
            i += 1;
        }
        if i >= n || bytes[i] != b'>' {
            return TagParse::NotTag;
        }
        return TagParse::Close {
            name_end,
            tag_end: i + 1,
        };
    }

    let mut self_closing = false;
    while i < n {
        match bytes[i] {
            b'>' => {
                return TagParse::Open {
                    name_end,
                    tag_end: i + 1,
                    is_self_closing: self_closing,
                };
            }
            b'/' => {
                self_closing = true;
                i += 1;
            }
            b'"' | b'\'' => {
                let quote = bytes[i];
                i += 1;
                while i < n && bytes[i] != quote {
                    i += 1;
                }
                if i >= n {
                    return TagParse::NotTag;
                }
                i += 1;
                self_closing = false;
            }
            _ => {
                if bytes[i].is_ascii_whitespace() {
                    self_closing = false;
                }
                i += 1;
            }
        }
    }

    TagParse::NotTag
}

#[inline]
fn is_name_start(b: u8) -> bool {
    b.is_ascii_alphabetic() || b == b'_'
}

#[inline]
fn is_name_cont(b: u8) -> bool {
    b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-' | b'.' | b':')
}

#[derive(Debug, Clone, Copy)]
struct Span {
    start: usize,
    end: usize,
    kind: SpanKind,
}

#[derive(Debug, Clone, Copy)]
enum SpanKind {
    Block,
    SelfClosing,
    OpenMarker,
    CloseMarker,
}

pub fn protect_tags(
    text: &str,
    compress_tagged_content: bool,
) -> (String, Vec<(String, String)>, ProtectStats) {
    let mut stats = ProtectStats::default();
    if text.is_empty() || !text.contains('<') {
        return (text.to_string(), Vec::new(), stats);
    }

    let (prefix, salted) = pick_placeholder_prefix(text);
    stats.placeholder_collision_avoided = salted;

    let spans = identify_spans(text, compress_tagged_content, &mut stats);

    match emit_output(text, &spans, &prefix) {
        Some((cleaned, blocks)) => (cleaned, blocks, stats),
        None => (text.to_string(), Vec::new(), stats),
    }
}

fn identify_spans(
    text: &str,
    compress_tagged_content: bool,
    stats: &mut ProtectStats,
) -> Vec<Span> {
    let bytes = text.as_bytes();
    let n = bytes.len();
    let mut spans: Vec<Span> = Vec::new();
    let mut stack: Vec<OpenTag> = Vec::new();

    let mut i = 0;
    while i < n {
        let b = bytes[i];
        if b != b'<' {
            i = memchr(b'<', &bytes[i..]).map(|j| i + j).unwrap_or(n);
            continue;
        }

        match parse_tag_at(bytes, i) {
            TagParse::NotTag => {
                i += 1;
            }
            TagParse::Open {
                name_end,
                tag_end,
                is_self_closing,
            } => {
                stats.tags_seen += 1;
                let name = &text[i + 1..name_end];
                if is_known_html_tag(name) {
                    stats.html_tags_skipped += 1;
                    i = tag_end;
                    continue;
                }
                if is_self_closing {
                    spans.push(Span {
                        start: i,
                        end: tag_end,
                        kind: SpanKind::SelfClosing,
                    });
                    stats.self_closing_protected += 1;
                    i = tag_end;
                    continue;
                }
                if compress_tagged_content {
                    spans.push(Span {
                        start: i,
                        end: tag_end,
                        kind: SpanKind::OpenMarker,
                    });
                }
                stack.push(OpenTag {
                    name_lower: name.to_ascii_lowercase(),
                    open_start: i,
                });
                i = tag_end;
            }
            TagParse::Close { name_end, tag_end } => {
                stats.tags_seen += 1;
                let close_name = &text[i + 2..name_end];
                if is_known_html_tag(close_name) {
                    stats.html_tags_skipped += 1;
                    i = tag_end;
                    continue;
                }
                let close_name_lower = close_name.to_ascii_lowercase();
                let matching = stack
                    .iter()
                    .rposition(|open| open.name_lower == close_name_lower);

                match matching {
                    Some(stack_idx) => {
                        if compress_tagged_content {
                            stack.truncate(stack_idx);
                            let _ = stack.pop();
                            spans.push(Span {
                                start: i,
                                end: tag_end,
                                kind: SpanKind::CloseMarker,
                            });
                        } else {
                            let open_start = stack[stack_idx].open_start;
                            stack.truncate(stack_idx);
                            spans.retain(|s| s.start < open_start);
                            spans.push(Span {
                                start: open_start,
                                end: tag_end,
                                kind: SpanKind::Block,
                            });
                            stats.custom_blocks_protected += 1;
                        }
                        i = tag_end;
                    }
                    None => {
                        stats.orphan_closes += 1;
                        i = tag_end;
                    }
                }
            }
        }
    }

    spans
}

fn emit_output(
    text: &str,
    spans: &[Span],
    prefix: &str,
) -> Option<(String, Vec<(String, String)>)> {
    let mut out = String::with_capacity(text.len());
    let mut blocks: Vec<(String, String)> = Vec::new();
    let mut cursor: usize = 0;

    for (counter, span) in (0_u64..).zip(spans.iter()) {
        if span.start < cursor {
            return None;
        }
        out.push_str(&text[cursor..span.start]);
        let placeholder = format!("{prefix}{counter}{PLACEHOLDER_SUFFIX}");
        let original = &text[span.start..span.end];
        blocks.push((placeholder.clone(), original.to_string()));
        out.push_str(&placeholder);
        cursor = span.end;
        let _ = span.kind;
    }
    out.push_str(&text[cursor..]);
    Some((out, blocks))
}

pub fn restore_tags(text: &str, blocks: &[(String, String)]) -> String {
    restore_tags_with_request_id(text, blocks, None)
}

pub fn restore_tags_with_request_id(
    text: &str,
    blocks: &[(String, String)],
    request_id: Option<&str>,
) -> String {
    if blocks.is_empty() {
        return text.to_string();
    }

    let mut result = text.to_string();
    let mut lost_count: usize = 0;
    let compressed_length = text.len();
    for (placeholder, original) in blocks {
        if result.contains(placeholder.as_str()) {
            result = result.replace(placeholder.as_str(), original);
        } else {
            lost_count += 1;
            tag_lost_error(original, compressed_length, request_id);
        }
    }
    let _ = lost_count;
    result
}

#[inline(never)]
fn tag_lost_error(original: &str, compressed_length: usize, request_id: Option<&str>) {
    let preview: String = original.chars().take(80).collect();
    match request_id {
        Some(rid) => tracing::error!(
            target: "headroom::tag_protector",
            event = "tag_protector_placeholder_lost",
            tag_preview = %preview,
            compressed_length = compressed_length,
            request_id = %rid,
            action = "discarded_wrap",
            "tag placeholder lost during compression — wrap discarded"
        ),
        None => tracing::error!(
            target: "headroom::tag_protector",
            event = "tag_protector_placeholder_lost",
            tag_preview = %preview,
            compressed_length = compressed_length,
            action = "discarded_wrap",
            "tag placeholder lost during compression — wrap discarded"
        ),
    }
}


#[inline]
fn memchr(needle: u8, haystack: &[u8]) -> Option<usize> {
    haystack.iter().position(|&b| b == needle)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn protect(text: &str) -> (String, Vec<(String, String)>) {
        let (cleaned, blocks, _stats) = protect_tags(text, false);
        (cleaned, blocks)
    }

    #[test]
    fn passthrough_when_no_angle_bracket() {
        let (cleaned, blocks) = protect("Just plain text");
        assert_eq!(cleaned, "Just plain text");
        assert!(blocks.is_empty());
    }

    #[test]
    fn html_tags_emitted_verbatim() {
        let text = "<div>Some content</div>";
        let (cleaned, blocks) = protect(text);
        assert_eq!(cleaned, text);
        assert!(blocks.is_empty());
    }

    #[test]
    fn html_tag_check_case_insensitive() {
        assert!(is_known_html_tag("DIV"));
        assert!(is_known_html_tag("Span"));
        assert!(!is_known_html_tag("system-reminder"));
        assert!(!is_known_html_tag("EXTREMELY_IMPORTANT"));
    }

    #[test]
    fn custom_tag_replaced_with_placeholder() {
        let text = "Before <system-reminder>Important</system-reminder> After";
        let (cleaned, blocks) = protect(text);
        assert!(!cleaned.contains("<system-reminder>"));
        assert!(!cleaned.contains("Important"));
        assert!(cleaned.contains("Before"));
        assert!(cleaned.contains("After"));
        assert_eq!(blocks.len(), 1);
        assert_eq!(blocks[0].1, "<system-reminder>Important</system-reminder>");
    }

    #[test]
    fn custom_tag_with_attributes() {
        let text = r#"<context key="session" type="persistent">user data</context>"#;
        let (_cleaned, blocks) = protect(text);
        assert_eq!(blocks.len(), 1);
        assert!(blocks[0].1.contains(r#"key="session""#));
    }

    #[test]
    fn self_closing_custom_tag() {
        let text = "Text <marker/> more text";
        let (_cleaned, blocks) = protect(text);
        assert_eq!(blocks.len(), 1);
        assert_eq!(blocks[0].1, "<marker/>");
    }

    #[test]
    fn self_closing_html_tag_not_protected() {
        let text = "Text <br/> more <hr/> text";
        let (cleaned, blocks) = protect(text);
        assert_eq!(cleaned, text);
        assert!(blocks.is_empty());
    }

    #[test]
    fn nested_custom_tags_collapse_to_outer_span() {
        let text = "<outer><inner>deep</inner></outer>";
        let (cleaned, blocks) = protect(text);
        assert!(!cleaned.contains("<outer>"));
        assert!(!cleaned.contains("<inner>"));
        assert_eq!(blocks.len(), 1);
        assert_eq!(blocks[0].1, "<outer><inner>deep</inner></outer>");
    }

    #[test]
    fn mixed_html_and_custom() {
        let text = "<div>HTML</div> <system-reminder>Rule</system-reminder> <p>HTML2</p>";
        let (cleaned, blocks) = protect(text);
        assert!(cleaned.contains("<div>"));
        assert!(cleaned.contains("<p>"));
        assert!(!cleaned.contains("<system-reminder>"));
        assert_eq!(blocks.len(), 1);
    }

    #[test]
    fn real_workflow_tags() {
        let cases = [
            "<tool_call>search({query: 'test'})</tool_call>",
            "<thinking>Let me analyze this</thinking>",
            "<EXTREMELY_IMPORTANT>Never skip validation</EXTREMELY_IMPORTANT>",
            "<user-prompt-submit-hook>check perms</user-prompt-submit-hook>",
            "<system-reminder>Rules</system-reminder>",
            "<result>Success: 42 items</result>",
        ];
        for tag in cases {
            let text = format!("Before {tag} After");
            let (_cleaned, blocks) = protect(&text);
            assert_eq!(blocks.len(), 1, "failed to protect: {tag}");
            assert_eq!(blocks[0].1, tag);
        }
    }

    #[test]
    fn empty_input_returns_empty() {
        let (cleaned, blocks) = protect("");
        assert!(cleaned.is_empty());
        assert!(blocks.is_empty());
    }

    #[test]
    fn compress_tagged_content_true_emits_marker_placeholders() {
        let text = "Before <system-reminder>Compressible content</system-reminder> After";
        let (cleaned, blocks, _stats) = protect_tags(text, true);
        assert!(!cleaned.contains("<system-reminder>"));
        assert!(!cleaned.contains("</system-reminder>"));
        assert!(cleaned.contains("Compressible content"));
        assert_eq!(blocks.len(), 2);
    }

    #[test]
    fn restore_basic() {
        let original = "Before <system-reminder>Rule</system-reminder> After";
        let (cleaned, blocks, _stats) = protect_tags(original, false);
        let restored = restore_tags(&cleaned, &blocks);
        assert_eq!(restored, original);
    }

    #[test]
    fn restore_empty_blocks_passthrough() {
        assert_eq!(restore_tags("untouched", &[]), "untouched");
    }

    #[test]
    fn restore_lost_placeholder_discards_wrap() {
        let blocks = vec![(
            "{{HEADROOM_TAG_0}}".to_string(),
            "<tag>data</tag>".to_string(),
        )];
        let compressed = "text without placeholder";
        let restored = restore_tags(compressed, &blocks);
        assert_eq!(restored, compressed);
        assert!(!restored.contains("<tag>"));
        assert!(!restored.contains("</tag>"));
        assert!(!restored.contains("<tag>data</tag>"));
    }

    #[test]
    fn restore_lost_placeholder_idempotent_when_all_missing() {
        let blocks = vec![
            ("{{HEADROOM_TAG_0}}".to_string(), "<a>1</a>".to_string()),
            ("{{HEADROOM_TAG_1}}".to_string(), "<b>2</b>".to_string()),
            ("{{HEADROOM_TAG_2}}".to_string(), "<c>3</c>".to_string()),
        ];
        let compressed = "compressor stripped every placeholder";
        let restored = restore_tags(compressed, &blocks);
        assert_eq!(restored, compressed);
    }

    #[test]
    fn restore_partial_loss_keeps_present_drops_lost() {
        let blocks = vec![
            ("{{HEADROOM_TAG_0}}".to_string(), "<a>1</a>".to_string()),
            (
                "{{HEADROOM_TAG_1}}".to_string(),
                "<lost>x</lost>".to_string(),
            ),
        ];
        let compressed = "head {{HEADROOM_TAG_0}} tail";
        let restored = restore_tags(compressed, &blocks);
        assert_eq!(restored, "head <a>1</a> tail");
        assert!(!restored.contains("<lost"));
        assert!(!restored.contains("</lost>"));
    }

    #[test]
    fn restore_roundtrip_preserves_content() {
        let original = "Start <system-reminder>Rule 1: validate</system-reminder> middle \
             <tool_call>search(q='test')</tool_call> end";
        let (cleaned, blocks, _stats) = protect_tags(original, false);
        let restored = restore_tags(&cleaned, &blocks);
        assert_eq!(restored, original);
    }


    #[test]
    fn fixed_in_3e4_replace_first_does_not_collide_on_duplicate_blocks() {
        let text = "<system-reminder>same</system-reminder> middle \
             <system-reminder>same</system-reminder>";
        let (cleaned, blocks, _stats) = protect_tags(text, false);
        assert_eq!(blocks.len(), 2);
        assert!(!cleaned.contains("<system-reminder>"));
        assert!(!cleaned.contains("</system-reminder>"));
        assert_ne!(blocks[0].0, blocks[1].0);
        assert_eq!(restore_tags(&cleaned, &blocks), text);
    }

    #[test]
    fn fixed_in_3e4_handles_50_plus_nested_custom_tags() {
        let depth = 60;
        let mut text = String::new();
        for _ in 0..depth {
            text.push_str("<lvl>");
        }
        text.push_str("core");
        for _ in 0..depth {
            text.push_str("</lvl>");
        }
        let (cleaned, blocks, _stats) = protect_tags(&text, false);
        assert!(!cleaned.contains("<lvl>"));
        assert!(!cleaned.contains("</lvl>"));
        assert_eq!(blocks.len(), 1);
        assert_eq!(restore_tags(&cleaned, &blocks), text);
    }

    #[test]
    fn fixed_in_3e4_self_closing_duplicates_get_distinct_placeholders() {
        let text = "<marker/> middle <marker/>";
        let (cleaned, blocks, _stats) = protect_tags(text, false);
        assert_eq!(blocks.len(), 2);
        assert_ne!(blocks[0].0, blocks[1].0);
        assert!(!cleaned.contains("<marker/>"));
        assert_eq!(restore_tags(&cleaned, &blocks), text);
    }

    #[test]
    fn fixed_in_3e4_placeholder_collision_is_avoided() {
        let text = "User wrote {{HEADROOM_TAG_0}} on purpose. \
             <system-reminder>real one</system-reminder>";
        let (_cleaned, blocks, stats) = protect_tags(text, false);
        assert!(stats.placeholder_collision_avoided);
        assert_eq!(blocks.len(), 1);
        assert_ne!(blocks[0].0, "{{HEADROOM_TAG_0}}");
    }


    #[test]
    fn orphan_close_tag_emitted_verbatim() {
        let text = "no opener </ghost> here";
        let (cleaned, blocks, stats) = protect_tags(text, false);
        assert_eq!(blocks.len(), 0);
        assert!(cleaned.contains("</ghost>"));
        assert_eq!(stats.orphan_closes, 1);
    }

    #[test]
    fn orphan_open_tag_emitted_verbatim() {
        let text = "<ghost>dangling content with no close";
        let (cleaned, blocks, _stats) = protect_tags(text, false);
        assert!(blocks.is_empty());
        assert_eq!(cleaned, text);
    }

    #[test]
    fn malformed_lone_lt_emitted_verbatim() {
        let text = "if a < b then c";
        let (cleaned, blocks, _stats) = protect_tags(text, false);
        assert_eq!(cleaned, text);
        assert!(blocks.is_empty());
    }

    #[test]
    fn truncated_close_marker_does_not_panic() {
        for text in ["</", "<", "<a/", "<a", "<a /", "</a"] {
            let (cleaned, blocks, _stats) = protect_tags(text, false);
            assert_eq!(cleaned, text);
            assert!(blocks.is_empty());
        }
    }

    #[test]
    fn attribute_with_gt_inside_quotes() {
        let text = r#"<context attr="a > b">payload</context>"#;
        let (cleaned, blocks, _stats) = protect_tags(text, false);
        assert_eq!(blocks.len(), 1);
        assert_eq!(blocks[0].1, text);
        assert!(!cleaned.contains("payload"));
    }

    #[test]
    fn html_close_inside_custom_block_does_not_pop_stack() {
        let text = "<custom>x</div> y</custom>";
        let (cleaned, blocks, stats) = protect_tags(text, false);
        assert_eq!(blocks.len(), 1);
        assert_eq!(blocks[0].1, "<custom>x</div> y</custom>");
        assert!(!cleaned.contains("<custom>"));
        assert_eq!(stats.html_tags_skipped, 1);
        assert_eq!(stats.orphan_closes, 0);
    }


    fn count_open_tags(s: &str) -> usize {
        let bytes = s.as_bytes();
        let mut count = 0_usize;
        let mut i = 0_usize;
        while i < bytes.len() {
            if bytes[i] != b'<' {
                i += 1;
                continue;
            }
            if i + 1 < bytes.len() && bytes[i + 1] == b'/' {
                i += 1;
                continue;
            }
            if i + 1 >= bytes.len() || !is_name_start(bytes[i + 1]) {
                i += 1;
                continue;
            }
            let mut j = i + 1;
            let mut self_closing = false;
            while j < bytes.len() && bytes[j] != b'>' {
                if bytes[j] == b'/' {
                    self_closing = true;
                }
                j += 1;
            }
            if j >= bytes.len() {
                break;
            }
            if !self_closing {
                count += 1;
            }
            i = j + 1;
        }
        count
    }

    fn count_close_tags(s: &str) -> usize {
        let bytes = s.as_bytes();
        let mut count = 0_usize;
        let mut i = 0_usize;
        while i < bytes.len() {
            if bytes[i] != b'<' {
                i += 1;
                continue;
            }
            if i + 1 >= bytes.len() || bytes[i + 1] != b'/' {
                i += 1;
                continue;
            }
            if i + 2 >= bytes.len() || !is_name_start(bytes[i + 2]) {
                i += 1;
                continue;
            }
            let mut j = i + 2;
            while j < bytes.len() && bytes[j] != b'>' {
                j += 1;
            }
            if j >= bytes.len() {
                break;
            }
            count += 1;
            i = j + 1;
        }
        count
    }

    proptest::proptest! {
        #[test]
        fn restore_never_introduces_asymmetry(content in "[a-z<>/]{0,200}") {
            let (cleaned, blocks, _stats) = protect_tags(&content, false);
            let mut stripped = cleaned.clone();
            for (placeholder, _original) in &blocks {
                stripped = stripped.replace(placeholder.as_str(), "");
            }
            let baseline_skew = count_open_tags(&stripped) as i64
                - count_close_tags(&stripped) as i64;

            let restored_all_lost = restore_tags(&stripped, &blocks);
            let lost_skew = count_open_tags(&restored_all_lost) as i64
                - count_close_tags(&restored_all_lost) as i64;
            proptest::prop_assert_eq!(
                lost_skew, baseline_skew,
                "discard-wrap path introduced asymmetry: baseline={}, after_restore={}, restored={:?}",
                baseline_skew, lost_skew, restored_all_lost
            );

            let restored_full = restore_tags(&cleaned, &blocks);
            let full_skew = count_open_tags(&restored_full) as i64
                - count_close_tags(&restored_full) as i64;
            let content_skew = count_open_tags(&content) as i64
                - count_close_tags(&content) as i64;
            proptest::prop_assert_eq!(
                full_skew, content_skew,
                "full-restore path drifted from input skew: input={}, restored={}",
                content_skew, full_skew
            );
        }

        #[test]
        fn restore_idempotent_when_all_placeholders_lost(
            content in "[a-z<>/]{0,200}",
            compressed in "[ -~]{0,200}",
        ) {
            let (_cleaned, blocks, _stats) = protect_tags(&content, false);
            let any_placeholder_present = blocks
                .iter()
                .any(|(p, _)| compressed.contains(p.as_str()));
            proptest::prop_assume!(!any_placeholder_present);
            let restored = restore_tags(&compressed, &blocks);
            proptest::prop_assert_eq!(restored, compressed);
        }

        #[test]
        fn restore_no_orphan_byte_injection(
            content in "[a-z<>/]{0,200}",
        ) {
            let (cleaned, blocks, _stats) = protect_tags(&content, false);
            let restored = restore_tags(&cleaned, &blocks);
            let substituted_original_bytes: usize = blocks
                .iter()
                .filter(|(p, _)| cleaned.contains(p.as_str()))
                .map(|(p, original)| original.len().saturating_sub(p.len()))
                .sum();
            let upper_bound = cleaned.len() + substituted_original_bytes;
            proptest::prop_assert!(
                restored.len() <= upper_bound,
                "restored too long: restored.len={} upper_bound={} cleaned.len={}",
                restored.len(), upper_bound, cleaned.len()
            );
        }
    }
}
