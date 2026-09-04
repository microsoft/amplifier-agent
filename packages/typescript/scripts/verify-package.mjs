import { createHash } from "node:crypto";
import { access, lstat, readFile, readdir } from "node:fs/promises";
import { constants } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

function verifyRuntimePath(relative) {
  const parts = relative.split(path.sep);
  if (parts.includes("conformance")) throw new Error("Rebuild a clean production runtime without conformance fixtures.");
  const bindingModule = /^(?:amplifier_agent|amplifier_agent_http)(?:\.|$)/;
  const bindingMetadata = /^amplifier[-_.]agent(?:[-_.]http)?(?:-[0-9].*)?\.(?:dist|egg)-info$/i;
  if (parts.some((part) => bindingModule.test(part) || bindingMetadata.test(part))) {
    throw new Error(`Rebuild the engine runtime without Python SDK or HTTP artifacts: ${relative}`);
  }
}

const root = fileURLToPath(new URL("../", import.meta.url));
for (const file of ["dist/index.js", "dist/index.d.ts", "LICENSE"]) {
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
await access(path.join(runtime, "amplifier-agent-engine"), constants.X_OK);
for (const [relative, expected] of Object.entries(manifest.files)) {
  if (path.isAbsolute(relative) || relative.split(path.sep).includes("..")) throw new Error("Runtime inventory contains a path outside the package.");
  const file = path.join(runtime, relative);
  const actual = createHash("sha256").update(await readFile(file)).digest("hex");
  if (actual !== expected) throw new Error(`Rebuild the runtime; its inventory does not match ${relative}.`);
}
