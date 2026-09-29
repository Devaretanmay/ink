//! Minimal PyO3 extension: package version only.
//!
//! The trajectory engine was removed in v0.4; the decision subsystem is
//! Python-owned. This module exists so the installed package keeps a stable
//! `microloop.microloop_core.version()` entry point.

use pyo3::prelude::*;

#[pyfunction]
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

#[pymodule]
fn microloop_core(_py: Python, module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(version, module)?)?;
    Ok(())
}
