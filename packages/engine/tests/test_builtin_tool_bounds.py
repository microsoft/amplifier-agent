from types import SimpleNamespace

from amplifier_agent_engine._engine.builtin_tools import adapt


class Recorder:
    name = "web_fetch"
    description = "Fetch content from a web URL."
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch content from."},
            "limit": {"type": "integer", "description": "Max bytes to return (default 200KB)."},
            "head_limit": {
                "type": "integer",
                "description": "Limit output to first N entries. "
                               "Set to 0 for unlimited (use with caution). "
                               "Use with total_matches field for pagination.",
            },
        },
        "required": ["url"],
    }

    def __init__(self) -> None:
        self.arguments = None

    async def execute(self, arguments):
        self.arguments = arguments
        return SimpleNamespace(success=True, output="fetched")


async def test_argument_above_the_bound_is_clamped_before_execution():
    tool = Recorder()
    registered = adapt(tool, bounds={"limit": 200 * 1024})
    assert await registered.handler(
        {"url": "https://example.test/report", "limit": 10_000_000}, None,
    ) == "fetched"
    assert tool.arguments == {"url": "https://example.test/report", "limit": 200 * 1024}


async def test_argument_below_the_bound_reaches_the_tool_unchanged():
    tool = Recorder()
    registered = adapt(tool, bounds={"limit": 200 * 1024})
    await registered.handler({"url": "https://example.test/report", "limit": 4096}, None)
    assert tool.arguments == {"url": "https://example.test/report", "limit": 4096}


async def test_zero_default_argument_is_removed_so_the_tool_default_applies():
    tool = Recorder()
    registered = adapt(tool, bounds={"head_limit": 500}, zero_defaults=("head_limit",))
    await registered.handler({"url": "https://example.test/report", "head_limit": 0}, None)
    assert tool.arguments == {"url": "https://example.test/report"}


def test_bounds_are_declared_in_the_schema_without_a_validation_maximum():
    registered = adapt(Recorder(), bounds={"limit": 200 * 1024, "head_limit": 500},
                       zero_defaults=("head_limit",))
    properties = registered.input_schema["properties"]
    assert "Values above 204800 are read as 204800." in properties["limit"]["description"]
    assert "Values above 500 are read as 500." in properties["head_limit"]["description"]
    assert "unlimited" not in properties["head_limit"]["description"]
    assert "maximum" not in properties["limit"] and "maximum" not in properties["head_limit"]
    assert Recorder.input_schema["properties"]["limit"]["description"] == (
        "Max bytes to return (default 200KB)."
    )
