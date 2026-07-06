
use std::fmt::Write;

use crate::transforms::pipeline::config::LogTemplateConfig;
use crate::transforms::pipeline::traits::{ReformatOutput, ReformatTransform, TransformError};
use crate::transforms::ContentType;

const NAME: &str = "log_template";
const WILDCARD: &str = "<*>";

pub struct LogTemplate {
    config: LogTemplateConfig,
}

impl LogTemplate {
    pub fn new(config: LogTemplateConfig) -> Self {
        Self { config }
    }
}

impl ReformatTransform for LogTemplate {
    fn name(&self) -> &'static str {
        NAME
    }

    fn applies_to(&self) -> &[ContentType] {
        &[ContentType::BuildOutput]
    }

    fn apply(&self, content: &str) -> Result<ReformatOutput, TransformError> {
        if content.is_empty() {
            return Err(TransformError::skipped(NAME, "empty input"));
        }
        let lines: Vec<&str> = content.lines().collect();
        if lines.len() < self.config.min_lines {
            return Err(TransformError::skipped(NAME, "input below min_lines"));
        }

        let tokenized: Vec<Vec<&str>> = lines.iter().map(|l| tokenize(l)).collect();

        let mut output = String::with_capacity(content.len());
        let mut next_template_id = 1usize;
        let mut run: Option<Run> = None;

        for (i, tokens) in tokenized.iter().enumerate() {
            if tokens.is_empty() {
                if let Some(r) = run.take() {
                    Self::flush_run(
                        &r,
                        &lines,
                        &tokenized,
                        &self.config,
                        &mut next_template_id,
                        &mut output,
                    );
                }
                output.push_str(lines[i]);
                output.push('\n');
                continue;
            }
            match run.as_mut() {
                Some(r) if Self::extends_run(r, tokens, self.config.similarity_threshold) => {
                    r.indices.push(i);
                    Self::merge_into_template(&mut r.template, tokens);
                }
                _ => {
                    if let Some(r) = run.take() {
                        Self::flush_run(
                            &r,
                            &lines,
                            &tokenized,
                            &self.config,
                            &mut next_template_id,
                            &mut output,
                        );
                    }
                    run = Some(Run::start(i, tokens));
                }
            }
        }
        if let Some(r) = run.take() {
            Self::flush_run(
                &r,
                &lines,
                &tokenized,
                &self.config,
                &mut next_template_id,
                &mut output,
            );
        }

        if content.ends_with('\n') {
        } else if output.ends_with('\n') {
            output.pop();
        }

        if output.len() >= content.len() {
            return Ok(ReformatOutput::from_lengths(
                content.len(),
                content.to_string(),
            ));
        }
        Ok(ReformatOutput::from_lengths(content.len(), output))
    }
}

impl LogTemplate {
    fn extends_run(run: &Run, tokens: &[&str], sim_threshold: f32) -> bool {
        if tokens.len() != run.template.len() {
            return false;
        }
        let len = tokens.len() as f32;
        let mut matches = 0usize;
        for (pos, tok) in tokens.iter().enumerate() {
            match &run.template[pos] {
                Some(constant) if constant == tok => matches += 1,
                None => matches += 1,
                _ => {}
            }
        }
        (matches as f32 / len) >= sim_threshold
    }

    fn merge_into_template(template: &mut [Option<String>], tokens: &[&str]) {
        for (pos, tok) in tokens.iter().enumerate() {
            if let Some(constant) = &template[pos] {
                if constant != tok {
                    template[pos] = None;
                }
            }
        }
    }

    fn flush_run(
        run: &Run,
        lines: &[&str],
        tokenized: &[Vec<&str>],
        cfg: &LogTemplateConfig,
        next_template_id: &mut usize,
        out: &mut String,
    ) {
        let constant_count = run.template.iter().filter(|t| t.is_some()).count();
        let varying_count = run.template.len() - constant_count;
        let collapse = run.indices.len() >= cfg.min_run
            && constant_count >= cfg.min_constant_tokens
            && varying_count > 0;

        if !collapse {
            for &i in &run.indices {
                out.push_str(lines[i]);
                out.push('\n');
            }
            return;
        }

        let template_id = *next_template_id;
        *next_template_id += 1;
        out.push_str("[Template T");
        let _ = write!(out, "{}", template_id);
        out.push_str(": ");
        for (pos, slot) in run.template.iter().enumerate() {
            if pos > 0 {
                out.push(' ');
            }
            match slot {
                Some(constant) => out.push_str(constant),
                None => out.push_str(WILDCARD),
            }
        }
        out.push_str("] (");
        let _ = write!(out, "{}", run.indices.len());
        out.push_str(" occurrences)\n");

        for &i in &run.indices {
            let toks = &tokenized[i];
            let mut first = true;
            for (pos, slot) in run.template.iter().enumerate() {
                if slot.is_none() {
                    if !first {
                        out.push(' ');
                    }
                    out.push_str(toks[pos]);
                    first = false;
                }
            }
            out.push('\n');
        }
    }
}

struct Run {
    indices: Vec<usize>,
    template: Vec<Option<String>>,
}

impl Run {
    fn start(idx: usize, tokens: &[&str]) -> Self {
        Self {
            indices: vec![idx],
            template: tokens.iter().map(|t| Some((*t).to_string())).collect(),
        }
    }
}

fn tokenize(line: &str) -> Vec<&str> {
    line.split_whitespace().collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::transforms::pipeline::config::PipelineConfig;

    fn cfg() -> LogTemplateConfig {
        PipelineConfig::default().reformat.log_template
    }

    fn reformat() -> LogTemplate {
        LogTemplate::new(cfg())
    }

    #[test]
    fn name_and_applies_to() {
        let r = reformat();
        assert_eq!(r.name(), "log_template");
        assert_eq!(r.applies_to(), &[ContentType::BuildOutput]);
    }

    #[test]
    fn empty_input_skipped() {
        let err = reformat().apply("").expect_err("empty must skip");
        match err {
            TransformError::Skipped { transform, .. } => assert_eq!(transform, "log_template"),
            _ => panic!("expected Skipped"),
        }
    }

    #[test]
    fn below_min_lines_skipped() {
        let log = "INFO a\nINFO b\nINFO c\n";
        let err = reformat().apply(log).expect_err("must skip");
        match err {
            TransformError::Skipped { .. } => {}
            _ => panic!("expected Skipped"),
        }
    }

    #[test]
    fn templated_run_collapses() {
        let mut log = String::new();
        for i in 0..50 {
            log.push_str(&format!(
                "2025-01-15T12:34:{:02} INFO worker-{} processing job {}\n",
                i,
                i,
                100 + i
            ));
        }
        let r = reformat().apply(&log).expect("must collapse");
        assert!(r.bytes_saved > 0);
        assert!(
            r.output.contains("[Template T1:"),
            "expected template header, got: {}",
            r.output.chars().take(200).collect::<String>()
        );
        assert!(r.output.contains("(50 occurrences)"));
        assert!(r.output.contains("worker-7"));
    }

    #[test]
    fn order_preserved_across_two_templates() {
        let mut log = String::new();
        for i in 0..12 {
            log.push_str(&format!("INFO worker-{i} starting\n"));
        }
        for i in 0..12 {
            log.push_str(&format!("WARN cache key-{i} expired\n"));
        }
        let r = reformat().apply(&log).expect("must collapse");
        let t1_pos = r.output.find("[Template T1:").expect("T1 header");
        let t2_pos = r.output.find("[Template T2:").expect("T2 header");
        assert!(t1_pos < t2_pos, "templates must be in input order");
        let t1_line = r.output[t1_pos..t2_pos].lines().next().unwrap();
        assert!(t1_line.contains("INFO"));
        assert!(t1_line.contains("starting"));
    }

    #[test]
    fn lossless_round_trip_via_template_and_variants() {
        let mut log = String::new();
        for i in 0..25 {
            log.push_str(&format!("TOK1 TOK2 var{i} TOK3\n"));
        }
        let r = reformat().apply(&log).expect("collapses");
        let mut iter = r.output.lines();
        let header = iter.next().unwrap();
        assert!(header.starts_with("[Template T1:"));
        let template_part = header
            .trim_start_matches("[Template T1: ")
            .split("] (")
            .next()
            .unwrap();
        let template_tokens: Vec<&str> = template_part.split_whitespace().collect();
        let var_pos = template_tokens
            .iter()
            .position(|t| *t == WILDCARD)
            .expect("must have wildcard");

        let mut reconstructed = Vec::new();
        for variant_line in iter {
            if variant_line.is_empty() {
                continue;
            }
            let var_tokens: Vec<&str> = variant_line.split_whitespace().collect();
            assert_eq!(var_tokens.len(), 1, "1 wildcard → 1 variant token");
            let mut full = template_tokens.clone();
            full[var_pos] = var_tokens[0];
            reconstructed.push(full.join(" "));
        }
        let original: Vec<String> = log.lines().map(|s| s.to_string()).collect();
        assert_eq!(reconstructed, original);
    }

    #[test]
    fn short_run_below_min_run_is_emitted_verbatim() {
        let mut log = String::new();
        for i in 0..10 {
            let toks: Vec<String> = (0..(i + 1) % 5 + 2).map(|j| format!("p{i}q{j}")).collect();
            log.push_str(&toks.join(" "));
            log.push('\n');
        }
        log.push_str("AAA worker-1 BBB\n");
        log.push_str("AAA worker-2 BBB\n");
        for i in 0..10 {
            let toks: Vec<String> = (0..(i + 1) % 4 + 2).map(|j| format!("s{i}t{j}")).collect();
            log.push_str(&toks.join(" "));
            log.push('\n');
        }
        let r = reformat().apply(&log).expect("input large enough");
        assert!(r.output.contains("AAA worker-1 BBB"));
        assert!(r.output.contains("AAA worker-2 BBB"));
    }

    #[test]
    fn all_unique_lines_are_emitted_verbatim() {
        let mut log = String::new();
        for i in 0..25 {
            log.push_str(&format!("event-{i} type-{i} status-{i}\n"));
        }
        let r = reformat().apply(&log).expect("processes");
        for i in 0..25 {
            assert!(
                r.output.contains(&format!("event-{i}")),
                "missing event-{i} in output"
            );
        }
    }

    #[test]
    fn blank_lines_break_runs() {
        let mut log = String::new();
        for i in 0..5 {
            log.push_str(&format!("INFO worker-{i} ready\n"));
        }
        log.push('\n');
        for i in 0..5 {
            log.push_str(&format!("INFO worker-{i} ready\n"));
        }
        for i in 0..15 {
            log.push_str(&format!("misc-{i}\n"));
        }
        let r = reformat().apply(&log).expect("input large enough");
        let t1_count = r.output.matches("[Template T1:").count();
        let t2_count = r.output.matches("[Template T2:").count();
        if t1_count == 1 && t2_count == 0 {
            assert!(
                !r.output.contains("(10 occurrences)"),
                "must not bridge the blank line"
            );
        }
    }

    #[test]
    fn never_inflates_output() {
        let mut log = String::new();
        for i in 0..30 {
            log.push_str(&format!("a{i}\n"));
        }
        let r = reformat().apply(&log).expect("processes");
        assert!(r.output.len() <= log.len());
    }

    #[test]
    fn unicode_tokens_survive() {
        let mut log = String::new();
        for i in 0..30 {
            log.push_str(&format!("INFO 🔥 worker-{i} héllo wörld\n"));
        }
        let r = reformat().apply(&log).expect("processes utf8");
        assert!(r.output.contains("🔥"));
        assert!(r.output.contains("héllo") || r.output.contains("wörld"));
    }

    #[test]
    fn template_with_no_constants_emits_verbatim() {
        let mut log = String::new();
        for i in 0..30 {
            log.push_str(&format!("{} {} {}\n", i, i + 1, i + 2));
        }
        let r = reformat().apply(&log).expect("processes");
        assert!(!r.output.contains("[Template"));
    }
}
