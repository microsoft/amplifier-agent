import { AgentError } from "../errors.js";
import type { Event } from "../records.js";

export class EventStream {
  readonly #queue: Event[] = [];
  readonly #ack: () => void;
  readonly #waiters = new Set<() => void>();
  #ended = false;
  #claimed = false;
  #abandoned = false;

  constructor(ack: () => void) { this.#ack = ack; }

  push(event: Event): void {
    if (this.#ended) return;
    if (this.#abandoned) this.#ack();
    else this.#queue.push(event);
    if (event.type === "terminal") this.#ended = true;
    for (const wake of this.#waiters) wake();
    this.#waiters.clear();
  }

  events(): AsyncIterable<Event> {
    if (this.#claimed) throw new AgentError({ code: "stream_already_consumed", category: "turn",
      message: "This turn already has an event consumer.", remedy: "Consume each turn's events through a single iterator.", retryable: false });
    this.#claimed = true;
    let started = false;
    return { [Symbol.asyncIterator]: () => {
      if (started) throw new AgentError({ code: "stream_already_consumed", category: "turn",
        message: "This event iterable already has a consumer.", remedy: "Keep one iterator for this turn.", retryable: false });
      started = true;
      return {
        next: async (): Promise<IteratorResult<Event>> => {
          while (!this.#queue.length && !this.#ended) await new Promise<void>((resolve) => { this.#waiters.add(resolve); });
          const value = this.#queue.shift();
          if (!value) return { done: true, value: undefined };
          this.#ack();
          return { done: false, value };
        },
        return: async (): Promise<IteratorResult<Event>> => {
          this.#abandoned = true;
          for (const _ of this.#queue.splice(0)) this.#ack();
          return { done: true, value: undefined };
        },
      };
    } };
  }
}
