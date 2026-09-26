//! Verifier comparison: distinguish progress, a plateau, and regression.

/// A verifier got worse relative to the best observed count in the same scope.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Regression {
    pub from_step: u64,
    pub from_failures: u64,
}

/// Compare the current failure count against the best (lowest) previous fresh
/// measurement. Returns `(progress, regression)`.
pub(crate) fn compare(previous: &[(u64, u64)], failures: u64) -> (bool, Option<Regression>) {
    let Some((best_step, best_failures)) = previous.iter().min_by_key(|(_, failures)| *failures)
    else {
        return (false, None);
    };
    if failures < *best_failures {
        (true, None)
    } else if failures > *best_failures {
        (
            false,
            Some(Regression {
                from_step: *best_step,
                from_failures: *best_failures,
            }),
        )
    } else {
        (false, None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_previous_measurement_is_neutral() {
        assert_eq!(compare(&[], 3), (false, None));
    }

    #[test]
    fn fewer_failures_is_progress() {
        assert_eq!(compare(&[(1, 4)], 3), (true, None));
    }

    #[test]
    fn more_failures_is_regression_against_the_best() {
        let (progress, regression) = compare(&[(1, 7), (2, 2)], 6);
        assert!(!progress);
        let regression = regression.expect("regression");
        assert_eq!(regression.from_step, 2);
        assert_eq!(regression.from_failures, 2);
    }
}
