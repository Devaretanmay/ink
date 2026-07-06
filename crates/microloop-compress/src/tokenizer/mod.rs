
mod estimator;
mod hf_impl;
mod registry;
mod tiktoken_impl;

pub use estimator::EstimatingCounter;
pub use hf_impl::{HfTokenizer, HfTokenizerError};
pub use registry::{
    clear_hf_registrations, detect_backend, get_tokenizer, register_hf, try_register_hf, Backend,
};
pub use tiktoken_impl::{TiktokenCounter, TiktokenError};

pub trait Tokenizer: Send + Sync + std::fmt::Debug {
    fn count_text(&self, text: &str) -> usize;

    fn backend(&self) -> Backend;
}
