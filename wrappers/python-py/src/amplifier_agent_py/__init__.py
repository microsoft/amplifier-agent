"""amplifier-agent-py — public entry point.

Python SDK for the Amplifier agent.  Spawns and drives the ``amplifier-agent``
CLI over a stdio protocol.  Mirrors the TypeScript wrapper
(``amplifier-agent-ts``) — same transport model, same config surface, same
error taxonomy.  Symmetry is enforced by the conformance suite under
``wrappers/conformance/``.

Install model: bring-your-own engine.  The ``amplifier-agent`` binary is NOT a
runtime dependency of this package.  Install it separately:

    uv tool install amplifier-agent
    # or
    pipx install amplifier-agent

The wrapper discovers the binary via ``AMPLIFIER_AGENT_BIN`` env var or
``shutil.which("amplifier-agent")`` and verifies its protocol version on
``spawn_agent()``.
"""

from __future__ import annotations

from ._api import PROTOCOL_VERSION_REQUIRED_BY_WRAPPER as PROTOCOL_VERSION_REQUIRED_BY_WRAPPER
from ._api import spawn_agent as spawn_agent
from .argv_builder import ApprovalMode as ApprovalMode
from .argv_builder import AssembleArgvInput as AssembleArgvInput
from .argv_builder import DisplayMode as DisplayMode
from .argv_builder import assemble_argv as assemble_argv
from .errors import AaaError as AaaError
from .errors import Classification as Classification
from .errors import Severity as Severity
from .mcp_spill import McpSpillResult as McpSpillResult
from .mcp_spill import cleanup_spill_file as cleanup_spill_file
from .mcp_spill import resolve_mcp_config_path as resolve_mcp_config_path
from .run_output_parser import STDERR_TAIL_BYTES as STDERR_TAIL_BYTES
from .run_output_parser import SubprocessOutcome as SubprocessOutcome
from .run_output_parser import parse_run_output as parse_run_output
from .session import DEFAULT_TIMEOUT_MS as DEFAULT_TIMEOUT_MS
from .session import SessionHandle as SessionHandle
from .session import SessionHandleParams as SessionHandleParams
from .spawn import BLOCKED_ENV_KEYS as BLOCKED_ENV_KEYS
from .spawn import DEFAULT_ALLOWLIST as DEFAULT_ALLOWLIST
from .spawn import EngineVersionPayload as EngineVersionPayload
from .spawn import build_env as build_env
from .spawn import probe_engine_version as probe_engine_version
from .spawn import resolve_binary_path as resolve_binary_path
from .sync import SyncSessionHandle as SyncSessionHandle
from .sync import spawn_agent_sync as spawn_agent_sync
from .transport import parse_ndjson_stream as parse_ndjson_stream
from .types import ActivityEvent as ActivityEvent
from .types import DisplayEvent as DisplayEvent
from .types import EngineInfo as EngineInfo
from .types import ErrorEvent as ErrorEvent
from .types import InitEvent as InitEvent
from .types import McpServerConfig as McpServerConfig
from .types import NotificationEvent as NotificationEvent
from .types import ResultEvent as ResultEvent
from .version import VersionCheckFail as VersionCheckFail
from .version import VersionCheckOk as VersionCheckOk
from .version import VersionCheckResult as VersionCheckResult
from .version import check_protocol_version as check_protocol_version

__all__ = [
    "BLOCKED_ENV_KEYS",
    "DEFAULT_ALLOWLIST",
    "DEFAULT_TIMEOUT_MS",
    "PROTOCOL_VERSION_REQUIRED_BY_WRAPPER",
    "STDERR_TAIL_BYTES",
    "AaaError",
    "ActivityEvent",
    "ApprovalMode",
    "AssembleArgvInput",
    "Classification",
    "DisplayEvent",
    "DisplayMode",
    "EngineInfo",
    "EngineVersionPayload",
    "ErrorEvent",
    "InitEvent",
    "McpServerConfig",
    "McpSpillResult",
    "NotificationEvent",
    "ResultEvent",
    "SessionHandle",
    "SessionHandleParams",
    "Severity",
    "SubprocessOutcome",
    "SyncSessionHandle",
    "VersionCheckFail",
    "VersionCheckOk",
    "VersionCheckResult",
    "assemble_argv",
    "build_env",
    "check_protocol_version",
    "cleanup_spill_file",
    "parse_ndjson_stream",
    "parse_run_output",
    "probe_engine_version",
    "resolve_binary_path",
    "resolve_mcp_config_path",
    "spawn_agent",
    "spawn_agent_sync",
]
