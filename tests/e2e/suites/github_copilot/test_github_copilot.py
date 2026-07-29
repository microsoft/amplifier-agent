"""DTU-backed tests for the GitHub Copilot provider.

Covers three model families served through the one Copilot provider (Anthropic, OpenAI and
Google backends), across single-shot replies, multi-turn continuity, tool calling, and the
``(GitHub)`` display-name suffix on ``/v1/models``.

These began as xfail-strict cases written before the provider existed, per
docs/E2E_TESTING.md, "Tests for features that do not exist yet". The provider landed and
they all passed, so the markers are gone and these are ordinary tests now.

``test_ghcp_token_reaches_dtu`` runs first and is the guard that keeps the rest honest: a
missing or unusable GITHUB_TOKEN makes every case below fail, and its failure message says
so directly instead of leaving you to infer it from three unrelated-looking reds.
"""

from __future__ import annotations

import pytest
from framework import dtu, harness
from framework.harness import E2ECase

from suites.github_copilot.cases import BASIC, LABEL, MULTITURN, TOOLCALL

pytestmark = pytest.mark.dtu


def _ids(cases: list[E2ECase]) -> list[str]:
    """Case names as pytest parametrize ids."""
    return [c.name for c in cases]


def test_ghcp_token_reaches_dtu(dtu_id: str) -> None:
    """The Copilot token is present inside the container and actually works there.

    Two things are proven at once, both of which the rest of the suite silently assumes:

    1. Passthrough worked. DTU bakes ``GITHUB_TOKEN`` into /etc/profile.d/dtu-env.sh with
       a bare ``if value:`` guard, so an unset host value yields no export and no error.
    2. api.github.com is reachable and the token carries Copilot entitlement. This matters
       specifically because the profile's ``github.com`` url_rewrite is an unanchored
       regex, so it also captures ``api.github.com`` and puts it behind TLS interception.

    The token is never echoed: it is piped into ``curl --config -`` so it appears in no
    argv, no log line, and no assertion message. Only three things ever reach stdout, and
    therefore ever reach an assertion message: ``token=present``, ``http=<code>``, and the
    ``copilot_plan`` field. The response body itself is written to a file, grepped, and
    deleted -- it carries entitlement and account metadata (login, org list, quota) but no
    credential material, verified by dumping it in the container on 2026-07-28.
    """
    script = (
        "set -eu\n"
        'test -n "${GITHUB_TOKEN:-}" || { echo "token=missing"; exit 3; }\n'
        "echo token=present\n"
        'code=$(printf \'header = "Authorization: token %s"\\n\' "$GITHUB_TOKEN" '
        "| curl -sS --config - -o /tmp/ghcp_user.json -w '%{http_code}' "
        "https://api.github.com/copilot_internal/user)\n"
        'echo "http=$code"\n'
        # GitHub pretty-prints this body, so do not assume spacing around the colon.
        "grep -o '\"copilot_plan\"[^,]*' /tmp/ghcp_user.json || true\n"
        # Only copilot_plan is ever read out of the response; drop the rest rather than
        # leaving account metadata (login, org list, quota) sitting in the container.
        "rm -f /tmp/ghcp_user.json\n"
    )
    result = dtu.exec_json(dtu_id, ["bash", "-lc", script])
    out = result.get("stdout", "")

    assert "token=missing" not in out, (
        "GITHUB_TOKEN is not set inside the DTU. Export it on the host before launching "
        "(`export GITHUB_TOKEN=$(gh auth token)`) and re-provision; the value is snapshotted "
        "at container launch, so an already-running DTU will not pick it up."
    )
    assert result.get("exit_code") == 0, f"token probe failed inside the DTU:\n{out}\n{result.get('stderr', '')}"
    assert "http=200" in out, (
        f"api.github.com/copilot_internal/user did not return 200 from inside the DTU. "
        f"A TLS error here points at the url_rewrites regex intercepting api.github.com.\n{out}"
    )
    assert "copilot_plan" in out, f"token authenticated but carries no Copilot entitlement:\n{out}"


@pytest.mark.parametrize("case", BASIC, ids=_ids(BASIC))
def test_ghcp_basic_reply(case: E2ECase, dtu_id: str, ghcp_env: dict[str, str]) -> None:
    harness.run_cli_case(dtu_id, case)


@pytest.mark.parametrize("case", MULTITURN, ids=_ids(MULTITURN))
def test_ghcp_multiturn(case: E2ECase, dtu_id: str, ghcp_env: dict[str, str]) -> None:
    harness.run_multi_case(dtu_id, case)


@pytest.mark.parametrize("case", TOOLCALL, ids=_ids(TOOLCALL))
def test_ghcp_toolcall(case: E2ECase, dtu_id: str, ghcp_env: dict[str, str]) -> None:
    harness.run_cli_case(dtu_id, case)


@pytest.mark.parametrize("case", LABEL, ids=_ids(LABEL))
def test_ghcp_models_labelled(case: E2ECase, dtu_id: str, server: dict[str, str]) -> None:
    harness.run_http_case(server["base_url"], server["token"], dtu_id, case)
