extern crate cbindgen;

use std::env;

fn main() {
    let crate_dir = env::var("CARGO_MANIFEST_DIR").unwrap();
    let out_dir = env::var("OUT_DIR").unwrap();
    let config_path = std::path::Path::new(&crate_dir).join("cbindgen.toml");

    let config = cbindgen::Config::from_file(&config_path).expect("cbindgen.toml not found");

    cbindgen::Builder::new()
        .with_crate(crate_dir)
        .with_config(config)
        .generate()
        .expect("Unable to generate bindings")
        .write_to_file(std::path::Path::new(&out_dir).join("microloop.h"));
}
