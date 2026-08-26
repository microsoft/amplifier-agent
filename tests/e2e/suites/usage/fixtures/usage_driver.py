#!/usr/bin/env python3
"""In-DTU driver: run ONE turn through ``amplifier_agent_py`` and describe the result.

Runs inside the DTU container, not on the host. Prints exactly ONE JSON line to
stdout describing the terminal ``DisplayEvent`` the wrapper produced, so the host-side
test can assert on the wrapper's PUBLIC surface without importing the SDK itself.

Contract with the caller:

* stdout  -- exactly one JSON object, on the last line. Nothing else.
* stderr  -- free-form diagnostics; the caller ignores it except when reporting.
* exit    -- always 0 when a report was produced. A non-zero exit means the driver
             itself could not run, which is an infrastructure failure, not a
             feature failure.

The driver NEVER asserts. It reports what it found (including which contract
attributes were absent) and lets the test decide. That keeps a missing attribute a
readable test failure rather than an opaque ``AttributeError`` traceback.

Usage:
    python3 usage_driver.py --session-id SID --prompt TEXT [--config PATH]
                            [--display-mode {text,ndjson}] [--engine-bin PATH]
                            [--stderr-tail-bytes {N|none}]

``--stderr-tail-bytes`` is deliberately tri-state: omitted means the kwarg is NOT
passed to ``spawn_agent_sync`` at all (exercising the default), ``none`` passes the
Python value ``None`` (full buffer), and an integer passes that integer.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import traceback
from typing import Any

# Environment the engine subprocess is allowed to inherit.
#
# The SDK's DEFAULT_ALLOWLIST is deliberately minimal (PATH/HOME/USER/LANG/TERM/TMPDIR)
# and carries no provider credential, so a turn spawned with the default allowlist has
# no API key and fails for a reason that has nothing to do with token usage. The extra
# names below are what the DTU itself puts in the environment: the provider credential,
# and the TLS/proxy variables the container's interception proxy needs.
ENGINE_ENV_ALLOWLIST = [
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "TERM",
    "TMPDIR",
    "ANTHROPIC_API_KEY",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "UV_NATIVE_TLS",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
]

# Attributes ResultEvent / ErrorEvent are specified to carry once per-turn usage lands.
CONTRACT_ATTRS = ("session_id", "turn_id", "exit_code", "usage", "stderr_tail")

# Attributes the Usage value object is specified to carry.
USAGE_ATTRS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd")

# Unicode REPLACEMENT CHARACTER. Its presence in a byte-bounded stderr tail means the
# slice landed mid-codepoint and was decoded with errors="replace".
REPLACEMENT_CHAR = "\ufffd"

_ABSENT = object()


def _emit(payload: dict[str, Any]) -> None:
    """Write the single-line JSON report to stdout."""
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    sys.stdout.flush()


def _note(message: str) -> None:
    """Driver diagnostics go to stderr so stdout stays a single JSON line."""
    sys.stderr.write(f"[usage_driver] {message}\n")


def _describe_usage(usage: Any) -> dict[str, Any]:
    """Describe a Usage value object without assuming any attribute exists."""
    missing: list[str] = []
    values: dict[str, Any] = {}

    for name in USAGE_ATTRS:
        value = getattr(usage, name, _ABSENT)
        if value is _ABSENT:
            missing.append(name)
            continue
        if name == "cost_usd":
            # Decimal is not JSON-serializable, and the test needs to know the type it
            # arrived as, so report both the rendering and the type name.
            values["cost_usd"] = None if value is None else str(value)
            values["cost_usd_type"] = type(value).__name__
            continue
        values[name] = value if isinstance(value, int) else str(value)

    return {"missing_attrs": missing, "values": values, "repr": repr(usage)}


def _describe_event(event: Any) -> dict[str, Any]:
    """Describe the terminal DisplayEvent, recording which contract attrs are absent."""
    missing: list[str] = []
    report: dict[str, Any] = {
        "event_type": getattr(event, "type", None),
        "text": getattr(event, "text", None),
        "code": getattr(event, "code", None),
        "message": getattr(event, "message", None),
    }

    for name in CONTRACT_ATTRS:
        if getattr(event, name, _ABSENT) is _ABSENT:
            missing.append(name)

    report["missing_attrs"] = missing
    report["session_id"] = getattr(event, "session_id", None)
    report["turn_id"] = getattr(event, "turn_id", None)
    report["exit_code"] = getattr(event, "exit_code", None)

    usage = getattr(event, "usage", _ABSENT)
    if usage is _ABSENT or usage is None:
        report["usage"] = None
        report["usage_missing_attrs"] = []
    else:
        described = _describe_usage(usage)
        report["usage"] = described["values"]
        report["usage_missing_attrs"] = described["missing_attrs"]
        report["usage_repr"] = described["repr"]

    tail = getattr(event, "stderr_tail", _ABSENT)
    if tail is _ABSENT or tail is None:
        report["stderr_tail"] = None
        report["stderr_tail_utf8_len"] = None
        report["stderr_tail_char_len"] = None
        report["stderr_tail_has_replacement_char"] = False
        report["stderr_tail_is_ascii"] = True
    else:
        text = str(tail)
        report["stderr_tail"] = text
        report["stderr_tail_utf8_len"] = len(text.encode("utf-8"))
        report["stderr_tail_char_len"] = len(text)
        report["stderr_tail_has_replacement_char"] = REPLACEMENT_CHAR in text
        report["stderr_tail_is_ascii"] = text.isascii()

    return report


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--config", default=None)
    parser.add_argument("--display-mode", choices=("text", "ndjson"), default=None)
    parser.add_argument("--engine-bin", default=None)
    parser.add_argument(
        "--stderr-tail-bytes",
        default=None,
        help="Integer byte cap, or the literal 'none' for the full buffer. Omit to exercise the default.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Pin the engine binary explicitly when the caller resolved one, so binary
    # discovery can never be the reason a usage assertion fails.
    if args.engine_bin:
        os.environ["AMPLIFIER_AGENT_BIN"] = args.engine_bin
    elif shutil.which("amplifier-agent"):
        os.environ.setdefault("AMPLIFIER_AGENT_BIN", str(shutil.which("amplifier-agent")))

    try:
        from amplifier_agent_py import spawn_agent_sync
    except Exception as exc:  # pragma: no cover - reported, not raised
        _emit(
            {
                "ok": False,
                "error_kind": "sdk_import_failed",
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        )
        return 0

    kwargs: dict[str, Any] = {
        "session_id": args.session_id,
        "approval": {"mode": "yes"},
        "env": {"allowlist": ENGINE_ENV_ALLOWLIST},
    }
    if args.config:
        kwargs["config_path"] = args.config
    if args.display_mode:
        kwargs["display_mode"] = args.display_mode
    if args.stderr_tail_bytes is not None:
        raw = args.stderr_tail_bytes.strip().lower()
        kwargs["stderr_tail_bytes"] = None if raw == "none" else int(raw)

    requested_tail = "<default>" if args.stderr_tail_bytes is None else kwargs["stderr_tail_bytes"]
    _note(f"spawning session={args.session_id} display={args.display_mode} stderr_tail_bytes={requested_tail}")

    try:
        handle = spawn_agent_sync(**kwargs)
    except TypeError as exc:
        # The option does not exist on this build of the SDK. Report it as data so the
        # host-side test can name the missing option instead of showing a traceback.
        _emit(
            {
                "ok": False,
                "error_kind": "spawn_kwarg_rejected",
                "error": f"{type(exc).__name__}: {exc}",
                "rejected_kwargs": sorted(kwargs),
                "traceback": traceback.format_exc(),
            }
        )
        return 0
    except Exception as exc:  # pragma: no cover - reported, not raised
        _emit(
            {
                "ok": False,
                "error_kind": "spawn_failed",
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        )
        return 0

    terminal: Any = None
    try:
        with handle:
            for event in handle.submit(args.prompt):
                if getattr(event, "type", "") in ("result", "error"):
                    terminal = event
                    break
    except Exception as exc:  # pragma: no cover - reported, not raised
        _emit(
            {
                "ok": False,
                "error_kind": "submit_failed",
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        )
        return 0

    if terminal is None:
        _emit(
            {
                "ok": False,
                "error_kind": "no_terminal_event",
                "error": "the event stream ended without a 'result' or 'error' event",
            }
        )
        return 0

    report = _describe_event(terminal)
    report["ok"] = True
    report["error_kind"] = None
    report["error"] = None
    report["requested_session_id"] = args.session_id
    report["requested_stderr_tail_bytes"] = None if args.stderr_tail_bytes is None else kwargs["stderr_tail_bytes"]
    report["stderr_tail_bytes_requested"] = args.stderr_tail_bytes is not None
    _emit(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
