import os
import subprocess
import sys
from zipfile import ZipFile

import pytest

from conformance.surface.check import ROOT, imports, violations
from conformance.surface.check_packages import PACKAGES, check_wheel


def without_site_packages(package, *arguments):
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "packages" / package / "src"),
    }
    return subprocess.run(
        [sys.executable, "-S", *arguments],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )


def wheel(tmp_path, distribution, dependencies, extra=None):
    path = tmp_path / "package.whl"
    with ZipFile(path, "w") as archive:
        archive.writestr(PACKAGES[distribution] + "/__init__.py", "")
        archive.writestr(
            "package.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: "
            + distribution
            + "\n"
            + "".join("Requires-Dist: " + item + "\n" for item in dependencies),
        )
        if extra:
            archive.writestr(extra, "")
    return path


@pytest.mark.parametrize(
    "distribution,dependencies,extra,expected",
    [
        ("amplifier-agent", ["amplifier-agent-engine"], None, None),
        ("amplifier-agent-engine", ["amplifier-core==1.6.1"], None, None),
        ("amplifier-agent-http", ["amplifier-agent", "starlette"], None, None),
        (
            "amplifier-agent",
            ["amplifier-agent-engine"],
            "amplifier_agent/_engine/state.py",
            "execution implementation",
        ),
        (
            "amplifier-agent",
            ["amplifier-agent-engine"],
            "amplifier_agent_engine/__init__.py",
            "foreign wheel content",
        ),
        ("amplifier-agent-engine", ["amplifier-agent"], None, "depends on a projection"),
        (
            "amplifier-agent-engine",
            ["amplifier-core"],
            "conformance/fixtures/runtime.py",
            "foreign wheel content",
        ),
        ("amplifier-agent-http", ["amplifier-agent-engine"], None, "public SDK"),
    ],
)
def test_wheel_boundary_discriminators(tmp_path, distribution, dependencies, extra, expected):
    errors = check_wheel(wheel(tmp_path, distribution, dependencies, extra))
    assert any(expected in error for error in errors) if expected else not errors


def test_sdk_import_without_engine_and_construction_failure_are_public():
    program = """
import asyncio
import sys
import amplifier_agent as sdk
assert not any(name.startswith('amplifier_agent_engine') for name in sys.modules)
async def check():
    try:
        await sdk.create_agent(sdk.AgentOptions())
    except sdk.AgentError as error:
        assert type(error) is sdk.AgentError
        assert error.code == 'engine_unavailable'
    else:
        raise AssertionError('Construction accepted a missing engine')
asyncio.run(check())
"""
    result = without_site_packages("python", "-c", program)
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""


def test_relative_import_cannot_hide_engine_dependency_on_sdk():
    dependencies = imports("from amplifier_agent import TextPart", "amplifier_agent_engine")
    assert violations("amplifier_agent_engine._records", dependencies)
    dependencies = imports("from .. import _records", "amplifier_agent_engine._engine")
    assert not violations("amplifier_agent_engine._engine.state", dependencies)


def test_from_import_cannot_hide_http_dependency_on_sdk_private_module():
    dependencies = imports("from amplifier_agent import _records", "amplifier_agent_http")
    assert violations("amplifier_agent_http._app", dependencies)


def test_http_package_import_does_not_require_http_dependencies():
    result = without_site_packages("http", "-c", "import amplifier_agent_http")
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""


def test_service_names_missing_http_dependencies():
    result = without_site_packages("http", "-m", "amplifier_agent_http")
    assert result.returncode == 2
    assert "Install amplifier-agent-http" in result.stderr
    assert "Traceback" not in result.stderr
