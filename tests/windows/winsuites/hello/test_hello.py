"""Hello suite: a real model turn produces output inside a Windows container.

Self-skips without ANTHROPIC_API_KEY. An absent key is a fact about the
operator's machine, not a defect in Windows support, and the keyless smoke
suite already proves the install works.
"""

from __future__ import annotations

import pytest
from winframework import harness
from winframework.harness import WinCase

from winsuites.hello.cases import HELLO

pytestmark = pytest.mark.windows


@pytest.mark.parametrize("case", HELLO, ids=[c.name for c in HELLO])
def test_hello(case: WinCase, agent: str, anthropic_key: str) -> None:
    harness.run_cli_case(agent, case)
