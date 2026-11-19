# ACP deletion idempotence amendment

CANDIDATE for owner ratification. Governs only `acp-face/1`, sections 3 and 4.

## Evidence

The upstream ACP v1 Session Delete specification recommends successful deletion
when a session is already absent. The unmodified ACP TCK requirement
`ACP-DELETE-002` fails against Amplifier Agent 0.26.1: the face forwards the
library's `not_found` error as JSON-RPC `-32002`.

Repeated deletion can occur after a successful operation whose response was lost.
The caller needs to establish that the session is absent without treating that
already-achieved outcome as a failure.

## Proposed amendment

In section 3, after the existing deletion clause, add:

> Deleting an unknown or already deleted session succeeds without changing other
> sessions. This exception applies only to the library's `not_found` result for
> that deletion. Invalid identifiers, sessions in use, and storage errors retain
> their existing error mapping.

In section 4, clarify that the section 3 deletion exception is applied before
mapping library errors to JSON-RPC errors.

The library contract and Python, TypeScript, and HTTP deletion behavior remain
unchanged. The ACP face neither retries a failed deletion nor treats an uncertain
outcome as success.

At ratification, apply those clauses, update the ACP reference, and record the
owner's decision and date in the contract changelog together. This candidate
records no ratification.

## Verification

The ACP SDK regression fails on the prior face and passes on the candidate.
Coverage includes unknown and repeated deletion, refusal while another connection
holds a session, invalid paths, and preservation of other library errors. The
upstream TCK runs unchanged against an isolated local provider and session store.
