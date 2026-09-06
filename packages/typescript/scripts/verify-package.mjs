import { createHash } from "node:crypto";
import { access, lstat, readFile, readdir } from "node:fs/promises";
import { constants } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

function verifyRuntimePath(relative) {
  const parts = relative.split(path.sep);
  if (parts.includes("conformance")) throw new Error("Rebuild a clean production runtime without conformance fixtures.");
  if (parts.at(-1) === "direct_url.json" && parts.at(-2)?.endsWith(".dist-info")) {
    throw new Error(`Rebuild the runtime without direct_url.json installer metadata: ${relative}`);
  }
  if (parts.some((part, index) => part === "google" && parts[index + 1] === "genai" && parts[index + 2] === "tests")) {
    throw new Error("Rebuild the runtime without the Google SDK test suite.");
  }
  const bindingModule = /^(?:amplifier_agent|amplifier_agent_http)(?:\.|$)/;
  const bindingMetadata = /^amplifier[-_.]agent(?:[-_.]http)?(?:-[0-9].*)?\.(?:dist|egg)-info$/i;
  if (parts.some((part) => bindingModule.test(part) || bindingMetadata.test(part))) {
    throw new Error(`Rebuild the engine runtime without Python SDK or HTTP artifacts: ${relative}`);
  }
}

const root = fileURLToPath(new URL("../", import.meta.url));
async function relativeFiles(directory, prefix = "") {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const relative = path.join(prefix, entry.name);
    if (entry.isDirectory()) files.push(...await relativeFiles(path.join(directory, entry.name), relative));
    else if (entry.isFile()) files.push(relative);
    else throw new Error(`Package build contains a non-regular file: ${relative}`);
  }
  return files;
}
const expectedBuild = new Set((await relativeFiles(path.join(root, "src")))
  .filter(file => file.endsWith(".ts"))
  .flatMap(file => [file.replace(/\.ts$/, ".js"), file.replace(/\.ts$/, ".d.ts")]));
const actualBuild = new Set(await relativeFiles(path.join(root, "dist")));
if ([...expectedBuild].some(file => !actualBuild.has(file)) || [...actualBuild].some(file => !expectedBuild.has(file))) {
  throw new Error("Rebuild an empty dist directory from the current TypeScript sources before packing.");
}
const packageManifest = JSON.parse(await readFile(path.join(root, "package.json"), "utf8"));
for (const executable of [
  "runtime/linux-x64/amplifier-agent-engine",
  "runtime/linux-x64/_internal/copilot_runtime/copilot",
]) {
  if (!packageManifest.publishConfig?.executableFiles?.includes(executable)) {
    throw new Error(`Preserve the executable permission in the npm archive: ${executable}`);
  }
  await access(path.join(root, executable), constants.X_OK);
}
for (const file of ["dist/index.js", "dist/index.d.ts", "runtime/linux-x64/node-host/index.mjs", "README.md", "LICENSE"]) {
  await access(path.join(root, file));
}
const runtime = path.join(root, "runtime", "linux-x64");
for (const directory of [path.join(root, "runtime"), runtime]) {
  if (!(await lstat(directory)).isDirectory()) throw new Error(`Runtime directory must not be a symbolic link: ${directory}`);
}
const actualFiles = new Set();
const directories = [runtime];
while (directories.length) {
  const directory = directories.pop();
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    const relative = path.relative(runtime, file);
    verifyRuntimePath(relative);
    if (entry.isSymbolicLink()) throw new Error(`Runtime contains a symbolic link: ${relative}`);
    if (entry.isDirectory()) directories.push(file);
    else if (entry.isFile()) {
      if (relative !== "manifest.json") actualFiles.add(relative);
    } else throw new Error(`Runtime contains a non-regular file: ${relative}`);
  }
}
const manifest = JSON.parse(await readFile(path.join(runtime, "manifest.json"), "utf8"));
if (manifest.platform !== "linux-x64") throw new Error("Build the runtime for Linux x86_64 before packing.");
if (manifest.variant !== "engine") throw new Error("Build the production runtime before packing a distributable package.");
const listedFiles = new Set(Object.keys(manifest.files));
const missing = [...listedFiles].filter((file) => !actualFiles.has(file));
const extra = [...actualFiles].filter((file) => !listedFiles.has(file));
if (missing.length || extra.length) {
  throw new Error(`Runtime inventory does not match its files; missing: ${missing.join(", ") || "none"}; extra: ${extra.join(", ") || "none"}.`);
}
for (const [relative, expected] of Object.entries(manifest.files)) {
  if (path.isAbsolute(relative) || relative.split(path.sep).includes("..")) throw new Error("Runtime inventory contains a path outside the package.");
  const file = path.join(runtime, relative);
  const actual = createHash("sha256").update(await readFile(file)).digest("hex");
  if (actual !== expected) throw new Error(`Rebuild the runtime; its inventory does not match ${relative}.`);
}
