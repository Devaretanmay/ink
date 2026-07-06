#![allow(unsafe_op_in_unsafe_fn)]

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

#[pymodule]
fn microloop_core(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyMicroloop>()?;
    Ok(())
}
