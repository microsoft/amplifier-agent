import { failure, freeze } from "./records.mjs";

class EventStream {
  #queue = [];
  #ack;
  #waiters = /* @__PURE__ */ new Set();
  #ended = false;
  #claimed = false;
  #abandoned = false;
  constructor(ack) {
    this.#ack = ack;
  }
  push(event) {
    if (this.#ended) return;
    if (this.#abandoned) this.#ack();
    else this.#queue.push(event);
    if (event.type === "terminal") this.#ended = true;
    for (const wake of this.#waiters) wake();
    this.#waiters.clear();
  }
  events() {
    if (this.#claimed) throw failure({
      code: "stream_already_consumed",
      category: "turn",
      message: "This turn already has an event consumer.",
      remedy: "Consume each turn's events through a single iterator.",
      retryable: false
    });
    this.#claimed = true;
    let started = false;
    return { [Symbol.asyncIterator]: () => {
      if (started) throw failure({
        code: "stream_already_consumed",
        category: "turn",
        message: "This event iterable already has a consumer.",
        remedy: "Keep one iterator for this turn.",
        retryable: false
      });
      started = true;
      return {
        next: async () => {
          while (!this.#queue.length && !this.#ended) await new Promise((resolve) => {
            this.#waiters.add(resolve);
          });
          const value = this.#queue.shift();
          if (!value) return { done: true, value: void 0 };
          this.#ack();
          return { done: false, value };
        },
        return: async () => {
          this.#abandoned = true;
          for (const _ of this.#queue.splice(0)) this.#ack();
          return { done: true, value: void 0 };
        }
      };
    } };
  }
}
export {
  EventStream
};
