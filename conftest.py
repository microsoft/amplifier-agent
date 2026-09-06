import pytest


def pytest_addoption(parser):
    parser.addoption("--engine", choices=("production", "replacement"), default="production")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "production_only: exercises a production provider, catalog or implementation detail"
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("engine") != "replacement":
        return
    excluded = [item for item in items if item.get_closest_marker("production_only")]
    if excluded:
        config.hook.pytest_deselected(items=excluded)
        items[:] = [item for item in items if item not in excluded]


@pytest.fixture(autouse=True)
def selected_engine(request, monkeypatch):
    monkeypatch.setenv("CONFORMANCE_ENGINE", request.config.getoption("engine"))
