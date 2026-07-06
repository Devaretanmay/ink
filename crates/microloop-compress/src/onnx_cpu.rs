
#[cfg(any(target_arch = "x86", target_arch = "x86_64"))]
pub(crate) fn onnx_runtime_supported_by_cpu() -> bool {
    std::is_x86_feature_detected!("avx2")
}

#[cfg(not(any(target_arch = "x86", target_arch = "x86_64")))]
pub(crate) fn onnx_runtime_supported_by_cpu() -> bool {
    true
}
