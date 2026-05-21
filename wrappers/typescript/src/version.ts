/**
 * Protocol version check for the Amplifier Agent wrapper.
 *
 * checkProtocolVersion() compares the wrapper's compiled protocol version
 * constant against the version reported by the engine binary.
 *
 * On mismatch it returns ok=false with a remediation hint.
 * The check can be bypassed with allowSkew=true.
 */

/** Result when protocol versions match (or skew is allowed). */
export interface VersionCheckOk {
  ok: true;
}

/** Result when protocol versions mismatch and skew is not allowed. */
export interface VersionCheckFail {
  ok: false;
  code: "protocol_version_mismatch";
  remediation: string;
}

export type VersionCheckResult = VersionCheckOk | VersionCheckFail;

export interface CheckProtocolVersionOptions {
  /** The protocol version compiled into the wrapper. */
  wrapper: string;
  /** The protocol version reported by the engine binary. */
  engine: string;
  /** If true, bypass the version check and always return ok=true. */
  allowSkew?: boolean;
}

/**
 * Compare wrapper and engine protocol versions.
 *
 * @returns VersionCheckOk if they match, or if allowSkew is true.
 * @returns VersionCheckFail if they mismatch and allowSkew is false/unset.
 */
export function checkProtocolVersion(opts: CheckProtocolVersionOptions): VersionCheckResult {
  const { wrapper, engine, allowSkew = false } = opts;

  if (allowSkew || wrapper === engine) {
    return { ok: true };
  }

  return {
    ok: false,
    code: "protocol_version_mismatch",
    remediation:
      `Protocol version mismatch: wrapper expects '${wrapper}' but engine reports '${engine}'. ` +
      `Install a compatible engine version or set allowProtocolSkew: true / ` +
      `AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW=1 to allow-protocol-skew.`,
  };
}
