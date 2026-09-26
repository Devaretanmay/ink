//! Modular, deterministic non-progress detectors.
//!
//! Each detector is a pure function over the bounded [`crate::history::History`]
//! window. Detectors report [`crate::event::Evidence`]; [`crate::engine`]
//! synthesizes the final progress state.

pub(crate) mod error;
pub(crate) mod oscillation;
pub(crate) mod repetition;
pub(crate) mod stagnation;
pub(crate) mod verification;
