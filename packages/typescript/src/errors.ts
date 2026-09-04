interface ErrorRecord {
  code: string;
  category: string;
  message: string;
  remedy: string;
  retryable: boolean;
  correlation_id?: string;
  details?: unknown;
}

export class AgentError extends Error {
  readonly code: string;
  readonly category: string;
  readonly remedy: string;
  readonly retryable: boolean;
  readonly correlation_id?: string;
  readonly details?: unknown;

  constructor(record: ErrorRecord) {
    super(record.message);
    this.name = "AgentError";
    this.code = record.code;
    this.category = record.category;
    this.remedy = record.remedy;
    this.retryable = record.retryable;
    if (record.correlation_id !== undefined) this.correlation_id = record.correlation_id;
    if ("details" in record) this.details = record.details;
    for (const [key, value] of Object.entries(record)) {
      if (!["code", "category", "message", "remedy", "retryable", "correlation_id", "details"].includes(key)) {
        Object.defineProperty(this, key, { value, enumerable: true });
      }
    }
  }
}

export class ToolFailed extends Error {
  constructor(message: string) { super(message); this.name = "ToolFailed"; }
}
export class ToolOutcomeUnknown extends Error {
  constructor(message: string) { super(message); this.name = "ToolOutcomeUnknown"; }
}
