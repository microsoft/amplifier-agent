import pytest

from tests.support.credentials import anthropic_only


@pytest.fixture(autouse=True)
def default_provider_credentials():
    # An unset provider defaults from credentials; an Anthropic key alone keeps it anthropic.
    # A separate patch, so a test undoing its own monkeypatch keeps these.
    with pytest.MonkeyPatch.context() as patch:
        anthropic_only(patch)
        yield
