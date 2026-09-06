"""Launch this binding's native installed consumers."""

import asyncio
import json
from pathlib import Path

from tests.e2e.installed.support import artifact, consumer_environment, start_consumer, stop


async def caller(provider, url, directory, mode):
    executable = artifact("AMPLIFIER_AGENT_NODE_EXECUTABLE")
    project = artifact("AMPLIFIER_AGENT_NODE_PROJECT")
    source = Path(__file__).with_name("production.mjs").read_text()
    arguments = ["--input-type=module", "--eval", source]
    environment = {
        **consumer_environment(provider, url, directory),
        "E2E_MODE": mode,
        "E2E_SESSION_ID": "installed-session",
        "E2E_EFFECT_LEDGER": str(directory / "effects.jsonl"),
        "E2E_EXPECTED_HISTORY": str(directory / "expected-history.json"),
    }
    return await start_consumer(
        executable, arguments, project, environment, directory / f"typescript-{mode}.log"
    )


async def probe(provider, url, directory, specification, config=None):
    executable = artifact("AMPLIFIER_AGENT_NODE_EXECUTABLE")
    project = artifact("AMPLIFIER_AGENT_NODE_PROJECT")
    environment = consumer_environment(provider, url, directory)
    if config is not None:
        Path(environment["AMPLIFIER_AGENT_CONFIG"]).write_text(json.dumps(config))
    environment["E2E_CONTRACT_PROBE"] = json.dumps(specification)
    source = Path(__file__).with_name("contract_probe.mjs").read_text()
    arguments = ["--input-type=module", "--eval", source]
    child = await asyncio.create_subprocess_exec(
        executable,
        *arguments,
        cwd=project,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(child.communicate(), 40)
        assert child.returncode == 0, stderr.decode()
    finally:
        await stop(child)
    return json.loads(stdout.decode().splitlines()[-1])
