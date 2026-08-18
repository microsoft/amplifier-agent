"""Suite fixtures: credential gating and host-config seeding.

Unlike ``github_copilot``, which fails loud on a missing credential, this suite
SKIPS. The two suites are answering different questions. github_copilot exists
to exercise a provider someone deliberately set out to test, so a missing token
there is a setup mistake worth stopping on. gemini's cases mostly guard the
provider catalog, which everyone touches, so this suite gets pulled into every
full ``cli.py run``. Failing the whole run for a contributor who has no Google
key and no interest in gemini would make the default run red for a reason that
is not their change.

So: no key, no run, no failure. The skip message says exactly what to do.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from framework import dtu

from suites.gemini.cases import CONFIG_PATH, CREDENTIAL_VAR

FIXTURES = Path(__file__).parent / "fixtures"

#: Probes for the variable's presence inside the container without ever echoing
#: its value, so it cannot leak into a pytest report or a CI log.
_PROBE = f'if [ -n "${CREDENTIAL_VAR}" ]; then echo key=present; else echo key=missing; fi'

_SKIP_REASON = (
    f"{CREDENTIAL_VAR} is not set inside the DTU, so the gemini suite is skipped. "
    f"To run it: export {CREDENTIAL_VAR} on the host and re-provision "
    f"(`uv run python tests/e2e/framework/cli.py up`). The value is snapshotted into the "
    f"container at launch, so exporting it against an already-running DTU has no effect."
)


@pytest.fixture(scope="session")
def gemini_key(dtu_id: str) -> None:
    """Skip the whole suite unless the credential actually reached the DTU.

    Checked inside the container rather than on the host, because that is where
    it matters: the host can have the variable exported and the DTU still not
    have it if the container predates the export.
    """
    result = dtu.exec_json(dtu_id, ["bash", "-lc", _PROBE])
    if "key=present" not in str(result.get("stdout", "")):
        pytest.skip(_SKIP_REASON)


@pytest.fixture(scope="session")
def gemini_config(dtu_id: str, gemini_key: None) -> str:
    """Push the gemini host config into the DTU and return its in-container path.

    The fixture deliberately omits ``provider.config.default_model``. The agent
    holds no static model table (see docs/spec/providers-and-models.md), so the
    provider module supplies its own default; pinning an id here would rot the
    moment Google renames a model.
    """
    dtu.push_file(dtu_id, str(FIXTURES / "host-config-gemini.json"), CONFIG_PATH)
    return CONFIG_PATH
