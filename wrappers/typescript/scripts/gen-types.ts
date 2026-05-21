/**
 * Codegen script: generates src/types.ts from JSON Schema sources.
 *
 * Reads all *.schema.json files from src/amplifier_agent_lib/protocol/schemas,
 * compiles each to TypeScript using json-schema-to-typescript, deduplicates
 * referenced types, and appends an ErrorCode string-union.
 *
 * Usage: pnpm run gen:types
 */
import { readdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { compile } from "json-schema-to-typescript";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Path to the JSON Schema source directory
const SCHEMAS_DIR = resolve(
  __dirname,
  "../../../src/amplifier_agent_lib/protocol/schemas"
);
const OUTPUT_FILE = resolve(__dirname, "../src/types.ts");

const BANNER = `// GENERATED FILE — DO NOT HAND-EDIT.
// Regenerate with: cd wrappers/typescript && pnpm run gen:types
// Source: src/amplifier_agent_lib/protocol/schemas/*.schema.json
`;

/**
 * Deduplicate export blocks from a compiled TypeScript string.
 *
 * json-schema-to-typescript includes externally-referenced types in the
 * output of each schema compilation (e.g. compiling InitializeParams also
 * emits ClientInfo). When processing multiple schemas, this causes duplicate
 * export declarations. This function strips any export block whose name has
 * already been seen.
 */
function deduplicateExports(
  source: string,
  seenNames: Set<string>
): string {
  const lines = source.split("\n");
  const result: string[] = [];
  let braceDepth = 0;
  let skipping = false;
  let inDeclaration = false;

  for (const line of lines) {
    // At the top level, detect new export declarations
    if (braceDepth === 0 && !inDeclaration) {
      const exportMatch = line.match(
        /^export\s+(?:interface|type|enum|const)\s+(\w+)/
      );
      if (exportMatch) {
        const name = exportMatch[1]!;
        if (seenNames.has(name)) {
          skipping = true;
        } else {
          seenNames.add(name);
          skipping = false;
        }
        inDeclaration = true;
      } else {
        // Not an export declaration at top level: keep the line (e.g. blank line)
        if (!skipping) result.push(line);
        continue;
      }
    }

    if (!skipping) {
      result.push(line);
    }

    // Update brace depth AFTER deciding whether to keep/skip the line
    const opens = (line.match(/\{/g) ?? []).length;
    const closes = (line.match(/\}/g) ?? []).length;
    braceDepth += opens - closes;

    // When we return to top level, end the current declaration block
    if (braceDepth === 0 && inDeclaration) {
      inDeclaration = false;
      skipping = false;
    }
  }

  return result.join("\n");
}

async function main(): Promise<void> {
  const allFiles = await readdir(SCHEMAS_DIR);
  const schemaFiles = allFiles
    .filter((f) => f.endsWith(".schema.json"))
    .sort();

  const seenNames = new Set<string>();
  const chunks: string[] = [BANNER];

  // Compile each schema (excluding error_codes, handled separately below)
  for (const file of schemaFiles) {
    if (file === "error_codes.schema.json") continue;

    const schemaPath = join(SCHEMAS_DIR, file);
    const raw = await readFile(schemaPath, "utf-8");
    const schema = JSON.parse(raw) as Record<string, unknown>;

    const compiled = await compile(
      // json-schema-to-typescript expects JSONSchema4; cast required
      schema as Parameters<typeof compile>[0],
      (schema["title"] as string | undefined) ??
        file.replace(".schema.json", ""),
      {
        bannerComment: "",
        additionalProperties: false,
        cwd: SCHEMAS_DIR,
        declareExternallyReferenced: true,
        format: true,
        style: { printWidth: 100, singleQuote: false },
      }
    );

    const deduplicated = deduplicateExports(compiled, seenNames).trim();
    if (deduplicated) {
      chunks.push(deduplicated);
    }
  }

  // Append ErrorCode string-union from error_codes.schema.json
  const errorCodesPath = join(SCHEMAS_DIR, "error_codes.schema.json");
  const errorCodesRaw = await readFile(errorCodesPath, "utf-8");
  const errorCodesSchema = JSON.parse(errorCodesRaw) as {
    enum: string[];
  };
  const enumValues = errorCodesSchema.enum
    .map((v) => `  | "${v}"`)
    .join("\n");
  const errorCodeType = `export type ErrorCode =\n${enumValues};`;
  chunks.push(errorCodeType);

  const output = chunks.join("\n\n") + "\n";
  await writeFile(OUTPUT_FILE, output, "utf-8");
  console.log(`✓ Generated ${OUTPUT_FILE}`);
  console.log(`  Types exported: ${[...seenNames].join(", ")}`);
  console.log(`  ErrorCode: ${errorCodesSchema.enum.length} values`);
}

main().catch((err) => {
  console.error("gen-types failed:", err);
  process.exit(1);
});
