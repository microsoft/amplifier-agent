"""Consume provider-declared unfiltered listing without changing its defaults."""

from collections.abc import Mapping
from typing import Any

_ABSENT = object()


class UnsupportedModelListingError(ValueError):
    """The installed provider advertises an incompatible model listing contract."""


def listing_options(provider: Any) -> dict[str, Any]:
    """Use legacy no-argument listing only when no selector metadata is advertised."""
    describe = getattr(provider, "get_model_list_info", _ABSENT)
    if describe is _ABSENT:
        return {}
    if not callable(describe):
        raise UnsupportedModelListingError("The provider's model listing metadata is not callable.")
    info = describe()
    if (
        not isinstance(info, Mapping)
        or type(info.get("version")) is not int
        or info.get("version") != 1
        or info.get("source") != "provider-api"
        or info.get("filters_keyword") != "filters"
        or info.get("override") != "replace"
        or not isinstance(info.get("unfiltered"), Mapping)
        or info["unfiltered"]
    ):
        raise UnsupportedModelListingError("The provider does not advertise a supported live unfiltered model listing.")
    return {"filters": {}}
