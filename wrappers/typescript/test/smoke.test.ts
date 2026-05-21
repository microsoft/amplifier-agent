import { describe, it, expect } from "vitest";
import { PROTOCOL_VERSION_REQUIRED_BY_WRAPPER } from "../src/index.js";

describe("smoke", () => {
  it("exports the correct protocol version constant", () => {
    expect(PROTOCOL_VERSION_REQUIRED_BY_WRAPPER).toBe("2026-05-aaa-v0");
  });
});
