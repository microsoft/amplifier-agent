/**
 * Display adapter — onEvent push callback + sub-agent event filtering.
 *
 * Pattern reference: design §4.5 sub-agent leak control.
 *
 * `applyDisplayFilter(adapter)` returns a predicate `(ev: DisplayEvent) => boolean`.
 * Mode = adapter.subagentEvents ?? 'all'.
 * If mode === 'all': return true for every event.
 * If mode === 'none': return true only for events whose parentTurnId is null/undefined.
 */

import type { DisplayEvent } from "./session.js";

/** Sub-agent filter mode: 'all' keeps everything, 'none' drops sub-agent events. */
export type SubagentMode = "all" | "none";

/**
 * Display adapter supplied by the host.
 *
 * - `onEvent`: optional push callback invoked for every kept event (pull via iterator
 *   AND push via callback see the same filtered stream).
 * - `subagentEvents`: 'all' (default) keeps sub-agent events; 'none' suppresses them.
 *   Sub-agent events are identified by the presence of `parentTurnId` in the event.
 */
export interface DisplayAdapter {
  onEvent?: (event: DisplayEvent) => void;
  subagentEvents?: SubagentMode;
}

/**
 * Build a keep-predicate from a DisplayAdapter.
 *
 * @param adapter - Host-supplied DisplayAdapter (may have no properties set).
 * @returns A predicate `(ev: DisplayEvent) => boolean` that returns true iff
 *          the event should be delivered to the consumer.
 */
export function applyDisplayFilter(
  adapter: DisplayAdapter,
): (ev: DisplayEvent) => boolean {
  const mode: SubagentMode = adapter.subagentEvents ?? "all";

  if (mode === "all") {
    return (_ev: DisplayEvent): boolean => true;
  }

  // mode === 'none': suppress events that carry parentTurnId (sub-agent events).
  return (ev: DisplayEvent): boolean => ev.parentTurnId == null;
}
