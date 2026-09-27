//! Thin, dependency-light PyO3 bindings over `microloop-core`.
//!
//! The Rust layer deliberately mirrors the core types as JSON in/out and keeps
//! no policy logic of its own; the Pythonic surface lives in `microloop/__init__.py`.

use microloop::{
    Budget, Capabilities, ControllerConfig, Decision, Event, InterventionAction, Monitor,
    MonitorConfig, Policy, PolicyConfig, RuntimeAction, RuntimeController, RuntimeDecision,
    RuntimeState,
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

/// Parse an optional JSON array of runtime action names.
fn parse_available(input: Option<&str>) -> PyResult<Option<Vec<RuntimeAction>>> {
    match input {
        Some(text) if !text.trim().is_empty() => {
            let actions: Vec<RuntimeAction> = serde_json::from_str(text).map_err(|error| {
                PyValueError::new_err(format!("invalid available actions: {error}"))
            })?;
            Ok(Some(actions))
        }
        _ => Ok(None),
    }
}

#[pyclass(name = "Monitor")]
pub struct PyMonitor {
    inner: Monitor,
}

#[pymethods]
impl PyMonitor {
    #[new]
    #[pyo3(signature = (config_json=None, policy_json=None, budget_json=None, capabilities_json=None, controller_json=None))]
    fn new(
        config_json: Option<&str>,
        policy_json: Option<&str>,
        budget_json: Option<&str>,
        capabilities_json: Option<&str>,
        controller_json: Option<&str>,
    ) -> PyResult<Self> {
        let config: MonitorConfig = parse_json(config_json)?;
        let budget: Budget = parse_json(budget_json)?;
        let capabilities: Capabilities = parse_json(capabilities_json)?;
        let inner = match controller_json {
            Some(text) if !text.trim().is_empty() => {
                let controller: ControllerConfig = serde_json::from_str(text).map_err(|error| {
                    PyValueError::new_err(format!("invalid controller: {error}"))
                })?;
                Monitor::from_controller_config(config, controller, capabilities, budget)
            }
            _ => {
                let policy: PolicyConfig = parse_json(policy_json)?;
                Monitor::from_runtime_config(config, policy, capabilities, budget)
            }
        }
        .map_err(|error| PyValueError::new_err(format!("invalid monitor: {error}")))?;
        Ok(Self { inner })
    }

    #[pyo3(signature = (event_json, available_json=None))]
    fn observe_json(&mut self, event_json: &str, available_json: Option<&str>) -> PyResult<String> {
        let event: Event = serde_json::from_str(event_json)
            .map_err(|error| PyValueError::new_err(format!("invalid event: {error}")))?;
        let available = parse_available(available_json)?;
        let decision: Decision = self
            .inner
            .observe_with_available(event, available)
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

#[pyclass(name = "RuntimeController")]
pub struct PyRuntimeController {
    inner: RuntimeController,
}

#[pymethods]
impl PyRuntimeController {
    #[new]
    #[pyo3(signature = (config_json=None, budget_json=None, capabilities_json=None))]
    fn new(
        config_json: Option<&str>,
        budget_json: Option<&str>,
        capabilities_json: Option<&str>,
    ) -> PyResult<Self> {
        let config: ControllerConfig = parse_json(config_json)?;
        let budget: Budget = parse_json(budget_json)?;
        let capabilities: Capabilities = parse_json(capabilities_json)?;
        let inner = RuntimeController::with_config(config, capabilities, budget)
            .map_err(|error| PyValueError::new_err(format!("invalid controller: {error}")))?;
        Ok(Self { inner })
    }

    #[pyo3(signature = (decision_json, runtime_json, available_json=None))]
    fn decide_json(
        &mut self,
        decision_json: &str,
        runtime_json: &str,
        available_json: Option<&str>,
    ) -> PyResult<String> {
        let decision: Decision = serde_json::from_str(decision_json)
            .map_err(|error| PyValueError::new_err(format!("invalid decision: {error}")))?;
        let runtime: RuntimeState = match runtime_json.trim() {
            "" => RuntimeState::default(),
            text => serde_json::from_str(text)
                .map_err(|error| PyValueError::new_err(format!("invalid runtime: {error}")))?,
        };
        let available = parse_available(available_json)?;
        let result: RuntimeDecision = self.inner.decide_with_available(
            &decision,
            &runtime,
            available
                .map(|actions| actions.into_iter().collect())
                .as_ref(),
        );
        serde_json::to_string(&result)
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
    module.add_class::<PyRuntimeController>()?;
    module.add_function(wrap_pyfunction!(version, module)?)?;
    Ok(())
}
