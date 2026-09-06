import { execFile } from "node:child_process";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

test("contract: public callable and record types reject broken consumers", { timeout: 30_000 }, async () => {
  const packageRoot = fileURLToPath(new URL("../", import.meta.url));
  await promisify(execFile)(join(packageRoot, "node_modules/.bin/tsc"), ["--noEmit"], {
    cwd: packageRoot, timeout: 25_000,
  });
});
