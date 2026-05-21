/**
 * L14 safety net — client-side result/final synthesis (design §4.6 contract #1).
 *
 * If the engine emits a non-null reply in its turn/submit response BUT no
 * result/final notification was observed first, the wrapper synthesizes a
 * result/final-shaped DisplayEvent with synthesized: true.
 *
 * Branch A (engine emits result/final) → sawFinal=true → no synthesis.
 * Branch B (engine omits result/final) → sawFinal=false, reply≠null → synthesize.
 */

import type { DisplayEvent } from "./session.js";

/** Parameters for synthesizeFinalIfMissing. */
export interface SynthesizeParams {
  /** True if a result/final notification was already observed during the turn. */
  sawFinal: boolean;
  /** The reply text from the turn/submit response, or null if absent. */
  reply: string | null;
  sessionId: string;
  turnId: string;
}

/**
 * Synthesize a result/final DisplayEvent if the engine omitted it.
 *
 * Returns null if:
 * - sawFinal is true (result/final was already observed), OR
 * - reply is null (no reply to synthesize from)
 *
 * Otherwise returns a DisplayEvent with type='result/final' and synthesized=true.
 */
export function synthesizeFinalIfMissing({
  sawFinal,
  reply,
  sessionId,
  turnId,
}: SynthesizeParams): DisplayEvent | null {
  if (sawFinal || reply === null) {
    return null;
  }
  return {
    type: "result/final",
    sessionId,
    turnId,
    synthesized: true,
    payload: {
      sessionId,
      turnId,
      text: reply,
    },
  };
}
