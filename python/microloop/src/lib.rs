//! Thin, dependency-light PyO3 bindings over `microloop-core`.
//!
//! The Rust layer deliberately mirrors the core types as JSON in/out and keeps
//! no policy logic of its own; the Pythonic surface lives in `microloop/__init__.py`.

use microloop::{
    Decision, Event, InterventionAction, Monitor, MonitorConfig, Policy, PolicyConfig,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

fn parse_json<T: serde::de::DeserializeOwned + Default>(input: Option<&str>) -> PyResult<T> {
    match input {
        Some(text) if !text.trim().is_empty() => serde_json::from_str(text)
            .map_err(|error| PyValueError::new_err(format!("invalid configuration: {error}"))),
        _ => Ok(T::default()),
    }
}

#[pyclass(name = "Monitor")]
pub struct PyMonitor {
    inner: Monitor,
}

#[pymethods]
impl PyMonitor {
    #[new]
    #[pyo3(signature = (config_json=None, policy_json=None))]
    fn new(config_json: Option<&str>, policy_json: Option<&str>) -> PyResult<Self> {
        let config: MonitorConfig = parse_json(config_json)?;
        let policy: PolicyConfig = parse_json(policy_json)?;
        let inner = Monitor::from_policy_config(config, policy)
            .map_err(|error| PyValueError::new_err(format!("invalid monitor: {error}")))?;
        Ok(Self { inner })
    }

    fn observe_json(&mut self, event_json: &str) -> PyResult<String> {
        let event: Event = serde_json::from_str(event_json)
            .map_err(|error| PyValueError::new_err(format!("invalid event: {error}")))?;
        let decision: Decision = self
            .inner
            .observe(event)
            .map_err(|error| PyValueError::new_err(format!("observe failed: {error}")))?;
        serde_json::to_string(&decision)
            .map_err(|error| PyValueError::new_err(format!("serialization failed: {error}")))
    }
}

#[pyclass(name = "Policy")]
pub struct PyPolicy {
    inner: Policy,
}

#[pymethods]
impl PyPolicy {
    #[new]
    #[pyo3(signature = (config_json=None))]
    fn new(config_json: Option<&str>) -> PyResult<Self> {
        let config: PolicyConfig = parse_json(config_json)?;
        let inner = Policy::new(config)
            .map_err(|error| PyValueError::new_err(format!("invalid policy: {error}")))?;
        Ok(Self { inner })
    }

    fn evaluate_json(&mut self, decision_json: &str) -> PyResult<String> {
        let decision: Decision = serde_json::from_str(decision_json)
            .map_err(|error| PyValueError::new_err(format!("invalid decision: {error}")))?;
        let action: InterventionAction = self.inner.evaluate(&decision);
        serde_json::to_string(&action)
            .map_err(|error| PyValueError::new_err(format!("serialization failed: {error}")))
    }
}

#[pyfunction]
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

#[pymodule]
fn microloop_core(_py: Python, module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<PyMonitor>()?;
    module.add_class::<PyPolicy>()?;
    module.add_function(wrap_pyfunction!(version, module)?)?;
    Ok(())
}
