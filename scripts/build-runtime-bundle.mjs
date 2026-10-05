#!/usr/bin/env node
// Canonical release-time assembly for the Ink macOS arm64 runtime.
import { createHash } from "node:crypto";
import { createReadStream, cpSync, existsSync, lstatSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const output = resolve(root, ".ink/runtime-bundle");
const lock = join(root, "runtime/macos-arm64.lock");
const modelName = "ink-decision-v1";
const engineVersion = "0.6.0rc2";
const pythonVersion = "3.13.12";
const protocolVersion = 1;
const bridgeSource = resolve(process.env.INKWAY_BRIDGE_SOURCE ?? "../inkway/server/internal/ink/bridge.py");
const modelSource = resolve(process.env.INK_MODEL_SOURCE ?? ".ink/models/ink-decision-v1");

function run(command, args) {
  const result = spawnSync(command, args, { cwd: root, stdio: "inherit" });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} failed with exit ${result.status}`);
}
function digest(file) {
  return new Promise((resolveHash, reject) => {
    const hash = createHash("sha256");
    createReadStream(file).on("data", (chunk) => hash.update(chunk)).on("error", reject).on("end", () => resolveHash(hash.digest("hex")));
  });
}
function validateModel(rootDir, manifest) {
  if (manifest.name !== modelName || manifest.version !== "1.0.0" || manifest.weights_modified !== true || !manifest.trained_by || !manifest.sha256?.["model.safetensors"]) {
    throw new Error("release input is not the expected trained Ink model");
  }
  return Promise.all(Object.entries(manifest.sha256).map(async ([relative, expected]) => {
    if (relative.startsWith("/") || relative.split(/[\\/]/).includes("..") || await digest(join(rootDir, relative)) !== expected) {
      throw new Error(`trained model integrity check failed: ${relative}`);
    }
  }));
}

async function main() {
  if (!existsSync(bridgeSource)) throw new Error(`Inkway bridge source is required: ${bridgeSource}`);
  if (!existsSync(lock)) throw new Error(`pinned runtime dependency lock is missing: ${lock}`);
  const modelManifestPath = join(modelSource, "ink-model.json");
  if (!existsSync(modelManifestPath)) throw new Error(`trained model release input is missing: ${modelSource}`);
  const modelManifest = JSON.parse(readFileSync(modelManifestPath, "utf8"));
  await validateModel(modelSource, modelManifest);
  const sourceRevision = spawnSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" });
  if (sourceRevision.status !== 0) throw new Error("cannot identify Ink source revision");
  const dirty = spawnSync("git", ["status", "--porcelain", "--untracked-files=all"], { cwd: root, encoding: "utf8" });
  if (dirty.status !== 0 || dirty.stdout.trim()) throw new Error("Ink source checkout must be clean before building a release artifact");

  rmSync(output, { recursive: true, force: true });
  mkdirSync(output, { recursive: true });
  const uv = process.env.UV_BINARY || "uv";
  run(uv, ["python", "install", "--install-dir", join(output, "python"), pythonVersion, "--no-bin"]);
  const alias = join(output, "python/cpython-3.13-macos-aarch64-none");
  if (existsSync(alias) && lstatSync(alias).isSymbolicLink()) rmSync(alias);
  const python = join(output, `python/cpython-${pythonVersion}-macos-aarch64-none/bin/python3.13`);
  const sitePackages = join(output, "site-packages");
  mkdirSync(sitePackages, { recursive: true });
  run(uv, ["pip", "install", "--python", python, "--target", sitePackages, "--link-mode", "copy", "--constraint", lock, root]);
  rmSync(join(sitePackages, `ink-${engineVersion}.dist-info/direct_url.json`), { force: true });
  const model = join(output, "model");
  cpSync(modelSource, model, { recursive: true, dereference: true, filter: (path) => !path.endsWith(".DS_Store") });
  const packagedManifest = JSON.parse(readFileSync(join(model, "ink-model.json"), "utf8"));
  delete packagedManifest.base_checkpoint;
  packagedManifest.base_checkpoint = `${packagedManifest.base_model}@${packagedManifest.base_revision}`;
  writeFileSync(join(model, "ink-model.json"), `${JSON.stringify(packagedManifest, null, 2)}\n`);
  for (const file of ["MODEL_CARD.md", "LICENSE", "NOTICE"]) {
    cpSync(join(root, "python/ink/ink/internal/model", file), join(output, file));
  }
  const manifest = {
    format_version: 1,
    ink_version: engineVersion,
    python_version: pythonVersion,
    bridge_protocol_version: protocolVersion,
    model_name: modelName,
    model_version: modelManifest.version,
    model_trained_by: modelManifest.trained_by,
    model_weights_sha256: modelManifest.sha256["model.safetensors"],
    model_manifest_sha256: await digest(join(model, "ink-model.json")),
    runtime_lock_sha256: await digest(lock),
    platform: "darwin-arm64",
    source_revision: sourceRevision.stdout.trim(),
  };
  writeFileSync(join(output, "compatibility.json"), `${JSON.stringify(manifest, null, 2)}\n`);
  const pythonCheck = `import sys; sys.path.insert(0, ${JSON.stringify(sitePackages)}); import ink, mlx, numpy, tokenizers; assert ink.__version__ == ${JSON.stringify(engineVersion)}; print('Ink release runtime imports verified')`;
  run(python, ["-I", "-c", pythonCheck]);
  console.log(`[ink-runtime] staged ${engineVersion} + ${modelName} ${modelManifest.version} from ${sourceRevision.stdout.trim()}`);
}

main().catch((error) => {
  console.error(`[ink-runtime] ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
