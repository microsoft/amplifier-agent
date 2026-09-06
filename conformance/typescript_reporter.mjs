/** Preserve individual Node test outcomes and their source locations. */
export default async function* report(events) {
  for await (const { type, data } of events) {
    if (type === "test:pass" || type === "test:fail") {
      const error = data.details?.error;
      yield JSON.stringify({
        kind: "case",
        case: data.name,
        status: data.skip || data.todo ? "skipped" : type === "test:pass" ? "passed" : "failed",
        ...(data.file === undefined ? {} : { file: data.file }),
        ...(data.line === undefined ? {} : { line: data.line }),
        ...(data.column === undefined ? {} : { column: data.column }),
        nesting: data.nesting,
        ...(data.details?.type === undefined ? {} : { type: data.details.type }),
        ...(error === undefined ? {} : { failure: {
          message: error.message,
          type: error.failureType,
          ...(error.cause?.message === undefined ? {} : { cause: error.cause.message }),
        } }),
      }) + "\n";
    } else if (type === "test:summary") {
      yield JSON.stringify({ kind: "summary", success: data.success, counts: data.counts }) + "\n";
    }
  }
}
