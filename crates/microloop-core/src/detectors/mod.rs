//! Modular, deterministic non-progress detectors.
//!
//! Each detector is a pure function over the bounded [`crate::history::History`]
//! window. Detectors report [`crate::event::Evidence`]; [`crate::engine`]
//! synthesizes the final progress state.

pub mod error;
pub mod oscillation;
pub mod repetition;
pub mod stagnation;
pub mod verification;
