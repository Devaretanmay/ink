
use crate::auth_mode::AuthMode;


pub(crate) const VOLATILE_TOKEN_THRESHOLD_PAYG: u32 = 128;

pub(crate) const VOLATILE_TOKEN_THRESHOLD_SUBSCRIPTION: u32 = 32;

pub(crate) const MAX_LOSSY_RATIO_PAYG: f32 = 0.45;

pub(crate) const MAX_LOSSY_RATIO_SUBSCRIPTION: f32 = 0.25;

pub const CACHE_WRITE_MULTIPLIER: f32 = 1.25;

pub const CACHE_READ_MULTIPLIER: f32 = 0.1;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CompressionPolicy {
    pub live_zone_only: bool,

    pub cache_aligner_enabled: bool,

    pub volatile_token_threshold: u32,

    pub max_lossy_ratio: f32,

    pub toin_read_only: bool,
}


impl CompressionPolicy {
    pub fn for_mode(mode: AuthMode) -> Self {
        match mode {
            AuthMode::Payg => Self {
                live_zone_only: false,
                cache_aligner_enabled: true,
                volatile_token_threshold: VOLATILE_TOKEN_THRESHOLD_PAYG,
                max_lossy_ratio: MAX_LOSSY_RATIO_PAYG,
                toin_read_only: false,
            },
            AuthMode::OAuth => Self {
                live_zone_only: false,
                cache_aligner_enabled: true,
                volatile_token_threshold: VOLATILE_TOKEN_THRESHOLD_PAYG,
                max_lossy_ratio: MAX_LOSSY_RATIO_PAYG,
                toin_read_only: false,
            },
            AuthMode::Subscription => Self {
                live_zone_only: true,
                cache_aligner_enabled: false,
                volatile_token_threshold: VOLATILE_TOKEN_THRESHOLD_SUBSCRIPTION,
                max_lossy_ratio: MAX_LOSSY_RATIO_SUBSCRIPTION,
                toin_read_only: true,
            },
        }
    }

    pub fn live_zone_compression_enabled(&self) -> bool {
        true
    }

    pub fn net_mutation_gain(
        &self,
        delta_t: u32,
        suffix_tokens: u32,
        expected_reads: f32,
        p_alive: f32,
    ) -> f32 {
        let w = CACHE_WRITE_MULTIPLIER;
        let r = CACHE_READ_MULTIPLIER;
        let reads = expected_reads.max(0.0);
        let alive = if p_alive.is_nan() {
            1.0
        } else {
            p_alive.clamp(0.0, 1.0)
        };
        (delta_t as f32) * (w + r * (reads - 1.0))
            - alive * (w - r) * ((suffix_tokens as f32) + (delta_t as f32))
    }

    pub fn should_mutate_deep(
        &self,
        delta_t: u32,
        suffix_tokens: u32,
        expected_reads: f32,
        p_alive: f32,
    ) -> bool {
        self.net_mutation_gain(delta_t, suffix_tokens, expected_reads, p_alive) > 0.0
    }

    pub fn break_even_reads(&self, delta_t: u32, suffix_tokens: u32) -> f32 {
        if delta_t == 0 {
            return 0.0;
        }
        let w = CACHE_WRITE_MULTIPLIER;
        let r = CACHE_READ_MULTIPLIER;
        ((w - r) / r) * ((suffix_tokens as f32) / (delta_t as f32))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn payg_is_aggressive() {
        let p = CompressionPolicy::for_mode(AuthMode::Payg);
        assert!(!p.live_zone_only, "PAYG can touch outside live zone");
        assert!(p.cache_aligner_enabled, "PAYG runs cache aligner");
        assert!(p.live_zone_compression_enabled());
    }

    #[test]
    fn payg_tuning_fields_aggressive() {
        let p = CompressionPolicy::for_mode(AuthMode::Payg);
        assert_eq!(
            p.volatile_token_threshold, 128,
            "PAYG volatile threshold is the relaxed default; F2.2-followup will tune"
        );
        assert!(
            (p.max_lossy_ratio - 0.45).abs() < f32::EPSILON,
            "PAYG max_lossy_ratio caps lossy paths at 0.45; F2.2-followup will tune"
        );
        assert!(
            !p.toin_read_only,
            "PAYG keeps TOIN write-enabled — network effect feeds on PAYG traffic"
        );
    }

    #[test]
    fn oauth_matches_payg_today() {
        let oauth = CompressionPolicy::for_mode(AuthMode::OAuth);
        let payg = CompressionPolicy::for_mode(AuthMode::Payg);
        assert_eq!(
            oauth, payg,
            "F2.1+F2.2 ship OAuth=PAYG; F2.2-followup will diverge based on telemetry"
        );
    }

    #[test]
    fn subscription_disables_cache_aligner() {
        let p = CompressionPolicy::for_mode(AuthMode::Subscription);
        assert!(p.live_zone_only, "Subscription is live-zone-only");
        assert!(
            !p.cache_aligner_enabled,
            "Subscription MUST skip cache aligner — load-bearing for #327/#388"
        );
        assert!(
            p.live_zone_compression_enabled(),
            "Subscription still gets live-zone compression — closing the cache complaint must NOT mean shipping zero compression"
        );
    }

    #[test]
    fn subscription_tuning_fields_conservative() {
        let p = CompressionPolicy::for_mode(AuthMode::Subscription);
        assert_eq!(
            p.volatile_token_threshold, 32,
            "Subscription volatile threshold flags content earlier (cache stability)"
        );
        assert!(
            (p.max_lossy_ratio - 0.25).abs() < f32::EPSILON,
            "Subscription max_lossy_ratio caps lossy paths at 0.25 (conservative)"
        );
        assert!(
            p.toin_read_only,
            "Subscription MUST be TOIN read-only — load-bearing for keeping the learning pool consistent across cache-sensitive traffic"
        );
    }

    #[test]
    fn max_lossy_ratio_in_unit_interval() {
        for mode in [AuthMode::Payg, AuthMode::OAuth, AuthMode::Subscription] {
            let r = CompressionPolicy::for_mode(mode).max_lossy_ratio;
            assert!(
                (0.0..=1.0).contains(&r),
                "max_lossy_ratio for {mode:?} = {r} is outside [0.0, 1.0]"
            );
        }
    }


    #[test]
    fn net_gain_small_shave_deep_suffix_is_loss() {
        let p = CompressionPolicy::for_mode(AuthMode::Payg);
        let gain = p.net_mutation_gain(2_000, 50_000, 10.0, 1.0);
        assert!((gain - (-55_500.0)).abs() < 1.0, "gain = {gain}");
        assert!(!p.should_mutate_deep(2_000, 50_000, 10.0, 1.0));
    }

    #[test]
    fn net_gain_big_shave_shallow_suffix_is_win() {
        let p = CompressionPolicy::for_mode(AuthMode::Payg);
        let gain = p.net_mutation_gain(50_000, 10_000, 3.0, 1.0);
        assert!((gain - 3_500.0).abs() < 1.0, "gain = {gain}");
        assert!(p.should_mutate_deep(50_000, 10_000, 3.0, 1.0));
    }

    #[test]
    fn net_gain_no_suffix_edit_profitable_with_reads_remaining() {
        let p = CompressionPolicy::for_mode(AuthMode::Subscription);
        assert!(p.should_mutate_deep(1, 0, 1.0, 1.0));
        assert!(p.should_mutate_deep(2_000, 0, 1.0, 1.0));
        let boundary = p.net_mutation_gain(2_000, 0, 0.0, 1.0);
        assert!(boundary.abs() < f32::EPSILON, "boundary = {boundary}");
    }

    #[test]
    fn net_gain_cold_cache_ignores_suffix() {
        let p = CompressionPolicy::for_mode(AuthMode::Payg);
        assert!(p.should_mutate_deep(2_000, 50_000, 0.0, 0.0));
    }

    #[test]
    fn net_gain_clamps_out_of_range_inputs() {
        let p = CompressionPolicy::for_mode(AuthMode::Payg);
        let clamped = p.net_mutation_gain(2_000, 50_000, -5.0, 7.0);
        let reference = p.net_mutation_gain(2_000, 50_000, 0.0, 1.0);
        assert!((clamped - reference).abs() < f32::EPSILON);
    }

    #[test]
    fn net_gain_guards_nan_inputs() {
        let p = CompressionPolicy::for_mode(AuthMode::Payg);
        let guarded = p.net_mutation_gain(2_000, 50_000, f32::NAN, f32::NAN);
        assert!(guarded.is_finite());
        let reference = p.net_mutation_gain(2_000, 50_000, 0.0, 1.0);
        assert!((guarded - reference).abs() < f32::EPSILON);
    }

    #[test]
    fn break_even_reads_matches_research_anchor() {
        let p = CompressionPolicy::for_mode(AuthMode::Payg);
        let r = p.break_even_reads(2_000, 50_000);
        assert!((r - 287.5).abs() < 0.5, "break-even = {r}");
        let shallow = p.break_even_reads(50_000, 10_000);
        assert!((shallow - 2.3).abs() < 0.05, "break-even = {shallow}");
        assert_eq!(p.break_even_reads(0, 10_000), 0.0);
    }
}
