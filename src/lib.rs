#![allow(clippy::not_unsafe_ptr_arg_deref)]
#![allow(clippy::missing_safety_doc)]

pub mod state;

pub mod canonical;
pub mod config;
pub mod engine;
pub mod history;
pub mod tool_schemas;

use std::ffi::c_char;
pub use state::MicroloopState;
pub use history::LoopVerdict;

#[unsafe(no_mangle)]
pub extern "C" fn microloop_init(yaml_str: *const u8, yaml_len: usize) -> *mut MicroloopState {
    if yaml_str.is_null() {
        return core::ptr::null_mut();
    }
    let yaml_slice = unsafe { std::slice::from_raw_parts(yaml_str, yaml_len) };
    let yaml_string = match std::str::from_utf8(yaml_slice) {
        Ok(s) => s,
        Err(_) => return std::ptr::null_mut(),
    };

    match MicroloopState::new(yaml_string) {
        Ok(state) => {
            let b = Box::new(state);
            Box::into_raw(b)
        }
        Err(_) => std::ptr::null_mut(),
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn microloop_verify(
    state_ptr: *mut MicroloopState,
    tool_name: *const u8,
    tool_name_len: usize,
    args_json: *const u8,
    args_json_len: usize,
) -> u8 {
    if state_ptr.is_null() {
        return 0;
    }

    let state = unsafe { &mut *state_ptr };

    let tool_slice = if !tool_name.is_null() {
        unsafe { std::slice::from_raw_parts(tool_name, tool_name_len) }
    } else {
        &[]
    };

    let args_slice = if !args_json.is_null() {
        unsafe { std::slice::from_raw_parts(args_json, args_json_len) }
    } else {
        &[]
    };

    verify(state, tool_slice, args_slice)
}

pub fn verify(state: &mut MicroloopState, tool_slice: &[u8], args_slice: &[u8]) -> u8 {
    let tool_str = std::str::from_utf8(tool_slice).unwrap_or("").to_string();
    let args_str = std::str::from_utf8(args_slice).unwrap_or("").to_string();

    let max_repeats = state.get_effective_threshold(&tool_str);

    match state.history.check_loop(
        &tool_str,
        &args_str,
        state.ignore_args,
        max_repeats,
        state.history_window,
    ) {
        history::LoopVerdict::Allow => {}
        history::LoopVerdict::WarnOscillation(msg) => state.set_warning(&msg),
        history::LoopVerdict::BlockExactMatch(msg) | history::LoopVerdict::BlockOscillation(msg) => {
            state.set_error(&msg);
            return state.block_result();
        }
    }

    if let Err(msg) = state.engine.validate(&args_str) {
        state.set_error(msg);
        return state.block_result();
    }

    0
}

#[unsafe(no_mangle)]
pub extern "C" fn microloop_get_last_warning(state_ptr: *mut MicroloopState) -> *const c_char {
    if state_ptr.is_null() {
        return std::ptr::null();
    }
    let state = unsafe { &*state_ptr };
    if state.warning_buffer.is_empty() {
        return std::ptr::null();
    }
    state.warning_buffer.as_ptr() as *const c_char
}

#[unsafe(no_mangle)]
pub extern "C" fn microloop_get_last_error(state_ptr: *mut MicroloopState) -> *const c_char {
    if state_ptr.is_null() {
        return std::ptr::null();
    }
    let state = unsafe { &*state_ptr };
    if state.error_buffer.is_empty() {
        return std::ptr::null();
    }
    state.error_buffer.as_ptr() as *const c_char
}

#[unsafe(no_mangle)]
pub extern "C" fn microloop_free(state_ptr: *mut MicroloopState) {
    if !state_ptr.is_null() {
        unsafe {
            let _ = Box::from_raw(state_ptr);
        }
    }
}
