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

#[pyfunction]
#[pyo3(signature = (hash, db_path="microloop_ccr.db"))]
fn microloop_retrieve(hash: &str, db_path: &str) -> PyResult<String> {
    let conn = rusqlite::Connection::open(db_path)
        .map_err(|e| PyValueError::new_err(format!("Failed to open DB: {}", e)))?;
        
    let mut stmt = conn.prepare("SELECT original_content FROM ccr WHERE hash = ?1")
        .map_err(|e| PyValueError::new_err(format!("SQL Error: {}", e)))?;
        
    let mut rows = stmt.query([hash])
        .map_err(|e| PyValueError::new_err(format!("Query Error: {}", e)))?;
        
    if let Some(row) = rows.next().map_err(|e| PyValueError::new_err(format!("Row Error: {}", e)))? {
        let content: String = row.get(0).map_err(|e| PyValueError::new_err(format!("Col Error: {}", e)))?;
        Ok(content)
    } else {
        Ok("Error: Hash not found in CCR".to_string())
    }
}

#[pymodule]
fn microloop_core(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyMicroloop>()?;
    m.add_function(wrap_pyfunction!(microloop_retrieve, m)?)?;
    Ok(())
}
