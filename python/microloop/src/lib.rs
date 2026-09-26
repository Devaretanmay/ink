#![allow(unsafe_op_in_unsafe_fn)]

use microloop::monitor::{Decision, Event, Monitor, MonitorConfig};
use microloop::policy::{Intervention, Policy, PolicyConfig};
use microloop::state::MicroloopState;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[pyclass(name = "Microloop")]
pub struct PyMicroloop {
    state: MicroloopState,
}

#[pymethods]
impl PyMicroloop {
    #[new]
    fn new(yaml_config: &str) -> PyResult<Self> {
        let state = MicroloopState::new(yaml_config)
            .map_err(|e| PyValueError::new_err(format!("Failed to parse config: {}", e)))?;
        Ok(Self { state })
    }

    fn verify(&mut self, tool_name: &str, tool_args_json: &str) -> u8 {
        microloop::verify(
            &mut self.state,
            tool_name.as_bytes(),
            tool_args_json.as_bytes(),
        )
    }
}

#[pyclass(name = "Monitor")]
pub struct PyMonitor {
    inner: Monitor,
}

#[pymethods]
impl PyMonitor {
    #[new]
    #[pyo3(signature = (run_id, config_json=None))]
    fn new(run_id: String, config_json: Option<&str>) -> PyResult<Self> {
        let config: MonitorConfig = match config_json {
            Some(s) if !s.trim().is_empty() => serde_json::from_str(s)
                .map_err(|e| PyValueError::new_err(format!("Invalid MonitorConfig JSON: {e}")))?,
            _ => MonitorConfig::default(),
        };
        let inner = Monitor::new(run_id, config).map_err(PyValueError::new_err)?;
        Ok(Self { inner })
    }

    fn observe_json(&mut self, event_json: &str) -> PyResult<String> {
        let event: Event = serde_json::from_str(event_json)
            .map_err(|e| PyValueError::new_err(format!("Invalid Event JSON: {e}")))?;
        let decision: Decision = self
            .inner
            .observe(event)
            .map_err(|e| PyValueError::new_err(format!("Monitor observe failed: {e}")))?;
        serde_json::to_string(&decision)
            .map_err(|e| PyValueError::new_err(format!("Decision serialization failed: {e}")))
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
        let config: PolicyConfig = match config_json {
            Some(s) if !s.trim().is_empty() => serde_json::from_str(s)
                .map_err(|e| PyValueError::new_err(format!("Invalid PolicyConfig JSON: {e}")))?,
            _ => PolicyConfig::default(),
        };
        let inner = Policy::new(config).map_err(PyValueError::new_err)?;
        Ok(Self { inner })
    }

    fn apply_json(&mut self, decision_json: &str) -> PyResult<String> {
        let decision: Decision = serde_json::from_str(decision_json)
            .map_err(|e| PyValueError::new_err(format!("Invalid Decision JSON: {e}")))?;
        let intervention: Intervention = self
            .inner
            .apply(&decision)
            .map_err(|e| PyValueError::new_err(format!("Policy apply failed: {e}")))?;
        serde_json::to_string(&intervention)
            .map_err(|e| PyValueError::new_err(format!("Intervention serialization failed: {e}")))
    }
}

#[pymodule]
fn microloop_core(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyMicroloop>()?;
    m.add_class::<PyMonitor>()?;
    m.add_class::<PyPolicy>()?;
    Ok(())
}
