#!/usr/bin/env node
// Assemble a versioned Microloop runtime release from the integration-tested
// runtime directory. The directory is built by Issuway's pinned runtime
// builder; this script adds release identity, bridge protocol evidence, and
// archive checksums without reimplementing dependency/model assembly.
import { createHash } from "node:crypto";
import { createReadStream, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { execFileSync } from "node:child_process";

const value = (name) => {
  const i = process.argv.indexOf(name);
  return i < 0 ? undefined : process.argv[i + 1];
};
const runtimeDir = resolve(value("--runtime-dir") ?? "");
const bridgeFile = resolve(value("--bridge") ?? "");
const outDir = resolve(value("--out") ?? "dist/runtime-release");
const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const artifactVersion = value("--version");
if (!value("--runtime-dir") || !value("--bridge") || !artifactVersion) {
  throw new Error("usage: package-runtime-release.mjs --runtime-dir DIR --bridge FILE --version VERSION [--out DIR]");
}
const sourceManifest = JSON.parse(readFileSync(join(runtimeDir, "compatibility.json"), "utf8"));
const sourceRevision = execFileSync("git", ["rev-parse", "HEAD"], { cwd: repositoryRoot, encoding: "utf8" }).trim();
if (sourceManifest.source_revision !== sourceRevision) {
  throw new Error("runtime bundle source revision differs from the clean Microloop checkout");
}
const modelManifest = JSON.parse(readFileSync(join(runtimeDir, "model/microloop-model.json"), "utf8"));
if (sourceManifest.microloop_version !== "0.6.0rc1" || sourceManifest.model_name !== "microloop-decision-v1" || sourceManifest.model_version !== "1.0.0" || sourceManifest.bridge_protocol_version !== 1 || sourceManifest.platform !== "darwin-arm64") {
  throw new Error("runtime compatibility metadata does not match the release contract");
}
if (modelManifest.name !== sourceManifest.model_name || modelManifest.version !== sourceManifest.model_version || modelManifest.sha256?.["model.safetensors"] !== sourceManifest.model_weights_sha256) {
  throw new Error("trained model does not match runtime compatibility metadata");
}
const sha256 = (file) => new Promise((ok, fail) => {
  const hash = createHash("sha256");
  createReadStream(file).on("data", (chunk) => hash.update(chunk)).on("error", fail).on("end", () => ok(hash.digest("hex")));
});
for (const [name, expected] of Object.entries(modelManifest.sha256 ?? {})) {
  if (name.startsWith("/") || name.split(/[\\/]/).includes("..") || await sha256(join(runtimeDir, "model", name)) !== expected) {
    throw new Error(`invalid trained model file/checksum: ${name}`);
  }
}
if (!existsSync(join(runtimeDir, "python")) || !existsSync(join(runtimeDir, "site-packages")) || !existsSync(join(runtimeDir, "model/model.safetensors"))) {
  throw new Error("runtime directory is incomplete");
}

rmSync(outDir, { recursive: true, force: true });
mkdirSync(outDir, { recursive: true });
const stage = join(outDir, `microloop-runtime-${artifactVersion}-darwin-arm64`);
const copy = spawnSync("ditto", [runtimeDir, join(stage, "runtime")], { stdio: "inherit" });
if (copy.status !== 0) throw new Error("failed copying runtime directory");
mkdirSync(join(stage, "bridge"), { recursive: true });
const bridgeTarget = join(stage, "bridge/bridge.py");
const bridgeCopy = spawnSync("ditto", [bridgeFile, bridgeTarget], { stdio: "inherit" });
if (bridgeCopy.status !== 0) throw new Error("failed copying bridge source");
const bridgeHash = await sha256(bridgeTarget);
const releaseManifest = {
  artifact_version: artifactVersion,
  microloop_engine_version: sourceManifest.microloop_version,
  model_version: sourceManifest.model_version,
  model_identifier: sourceManifest.model_name,
  bridge_protocol_version: sourceManifest.bridge_protocol_version,
  platform: "darwin",
  architecture: "arm64",
  model_sha256: sourceManifest.model_weights_sha256,
  runtime_manifest_sha256: await sha256(join(stage, "runtime/compatibility.json")),
  bridge_sha256: bridgeHash,
  source_microloop_revision: sourceRevision,
  issuway_bridge_sha256: bridgeHash,
};
writeFileSync(join(stage, "manifest.json"), `${JSON.stringify(releaseManifest, null, 2)}\n`);
const zipName = `${basename(stage)}.zip`;
const zipPath = join(outDir, zipName);
const zipped = spawnSync("ditto", ["-c", "-k", "--keepParent", stage, zipPath], { stdio: "inherit" });
if (zipped.status !== 0) throw new Error("failed creating runtime release ZIP");
const archiveHash = await sha256(zipPath);
writeFileSync(`${zipPath}.sha256`, `${archiveHash}  ${zipName}\n`);
writeFileSync(join(outDir, "release.json"), `${JSON.stringify({ ...releaseManifest, archive: zipName, artifact_sha256: archiveHash }, null, 2)}\n`);
console.log(`[microloop-release] ${zipPath}\nsha256=${archiveHash}`);
