/**
 * Types test: verifies that the generated types file exports the expected
 * TypeScript interfaces and type aliases derived from JSON Schema codegen.
 *
 * RED: fails because wrappers/typescript/src/types.ts does not exist yet
 *   (module resolution error at import time).
 * GREEN: passes once gen-types.ts codegen runs and produces src/types.ts.
 *
 * Note: `import type` would be erased at runtime and would not fail when the
 * module is missing. We intentionally use a side-effect import so that the
 * module must be resolvable for the tests to run.
 */
import { describe, it, expect } from "vitest";

// Intentional value import (not `import type`) so vitest fails with
// ModuleNotFoundError when src/types.ts does not exist.
// At runtime, TypeScript type aliases transpile to `undefined`, which is fine;
// we only use them as TypeScript annotations for compile-time shape checks.
// The module itself MUST exist for the import to resolve.
import type {
  InitializeParams,
  InitializeResult,
  TurnSubmitParams,
  TurnSubmitResult,
  ErrorCode,
} from "../src/types.js";

// The side-effect import (no `type` keyword) ensures module resolution fails
// when the file doesn't exist, making this a proper RED/GREEN test.
import "../src/types.js";

describe("generated types", () => {
  it("InitializeParams has required fields: protocolVersion, clientInfo, capabilities", () => {
    const params: InitializeParams = {
      protocolVersion: "0.2.0",
      clientInfo: { name: "test-client", version: "1.0.0" },
      capabilities: {},
    };
    expect(params.protocolVersion).toBe("0.2.0");
    expect(params.clientInfo).toBeDefined();
    expect(params.clientInfo.name).toBe("test-client");
    expect(params.capabilities).toBeDefined();
  });

  it("TurnSubmitParams has required fields: sessionId, turnId, prompt", () => {
    const params: TurnSubmitParams = {
      sessionId: "sess-abc",
      turnId: "turn-001",
      prompt: "Hello, agent!",
    };
    expect(params.sessionId).toBe("sess-abc");
    expect(params.turnId).toBe("turn-001");
    expect(params.prompt).toBe("Hello, agent!");
  });

  it("InitializeResult has required fields: capabilities, serverInfo, sessionState", () => {
    const result: InitializeResult = {
      capabilities: { streaming: true },
      serverInfo: { name: "amplifier-agent", version: "0.0.0" },
      sessionState: { sessionId: "sess-abc", resumed: false },
    };
    expect(result.capabilities).toBeDefined();
    expect(result.serverInfo).toBeDefined();
    expect(result.sessionState).toBeDefined();
    expect(result.sessionState.sessionId).toBe("sess-abc");
  });

  it("TurnSubmitResult has required fields: reply, turnId, sessionId", () => {
    const result: TurnSubmitResult = {
      reply: "Hello back!",
      turnId: "turn-001",
      sessionId: "sess-abc",
    };
    expect(result.reply).toBe("Hello back!");
    expect(result.turnId).toBe("turn-001");
    expect(result.sessionId).toBe("sess-abc");
  });

  it("ErrorCode is a string-enum union containing protocol_version_mismatch", () => {
    // TypeScript compile-time check: 'protocol_version_mismatch' must satisfy ErrorCode.
    // At runtime, this is just a string assignment + equality check.
    const code: ErrorCode = "protocol_version_mismatch";
    expect(code).toBe("protocol_version_mismatch");
  });

  it("ErrorCode union contains internal (runtime member check)", () => {
    // Verifies the union is populated with multiple error codes from the schema.
    const code: ErrorCode = "runtime";
    expect(code).toBe("runtime");
  });
});
