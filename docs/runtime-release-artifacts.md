# Issuway runtime release artifacts

Microloop produces the versioned runtime archive that Issuway embeds. The SDK source, runtime dependency lock, runtime assembler, compatibility manifest, model integrity check, and archive checksum generation live in this repository. Issuway contains only the exact artifact pin and fetch/verification code; production packaging cannot use a source checkout or local model cache.

## Produce a release

Release from a clean Microloop checkout after validating the SDK changes and selecting the approved trained model artifact. The model must be available to the release builder under `.microloop/models/microloop-decision-v1` or `MICROLOOP_MODEL_SOURCE`; it is checked against its own `microloop-model.json` file hashes. The bridge is supplied from the matching Issuway source and included in the archive with its SHA-256.

```sh
make check
make runtime-release PLATFORM=darwin ARCH=arm64 VERSION=0.6.0rc1.1 \
  ISSUWAY_BRIDGE=/path/to/issuway/server/internal/microloop/bridge.py
```

The command builds a relocatable CPython 3.13.12 runtime with the exact pins in `runtime/macos-arm64.lock`, installs this Microloop source, copies the trained model, validates model and runtime imports, then emits:

- `dist/runtime-release/microloop-runtime-<version>-darwin-arm64.zip`
- the archive `.sha256` sidecar;
- `dist/runtime-release/release.json` with engine/model/protocol/platform, source revision, bridge hash, runtime-manifest hash, model hash, archive identity, and archive checksum.

Do not change an existing release asset or retarget an existing tag. Publish the new checksum-identified archive to a new immutable GitHub Release tag. The generated release-manifest under the archive records the data needed to make Issuway reject a wrong or incompatible runtime.

## Pin in Issuway

Update `apps/desktop/scripts/microloop-runtime.json` in the Issuway repository with the exact asset URL and `artifact_sha256` from `release.json`. Update the artifact, engine, model, protocol, and model-hash fields together. Run the desktop runtime fetch test, `make microloop-runtime`, packaging verification, and the mounted-DMG model/FastPath/persistence proof before shipping that Issuway revision.

Issuway's archive extractor verifies the archive bytes before extraction, checks every ZIP entry remains under the expected versioned root, validates release/runtime compatibility metadata and the model/bridge checksums, and rejects a bridge whose bytes differ from Issuway's bridge source. The per-user decision database remains outside app resources and is not part of the archive.

## Cross-repository chain

Microloop change → SDK tests → build versioned runtime from the locked source/model → package the Issuway bridge with the artifact → publish immutable release asset and checksum → update Issuway's exact pin → clean Issuway checkout fetches and verifies → desktop package → mounted-DMG inference/FastPath/restart proof.

The artifact is currently macOS arm64 only because the trained runtime uses MLX. Signing and notarization are separate Issuway distribution gates.
