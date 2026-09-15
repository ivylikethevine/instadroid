"""JSON with a real type: `json.loads` returns Any, so decoded values are checked all the way down and
typed as `JSON` instead. Shared by the scripts and the tests (the feed server has no JSON to read)."""

import json
from collections.abc import Callable
from typing import TypeIs

type JSON = dict[str, JSON] | list[JSON] | str | int | float | bool | None

# json.loads seen as returning `object`, not Any: as_json() then checks what it actually produced.
_decode: Callable[[str | bytes], object] = json.loads


def _is_list(value: object) -> TypeIs[list[object]]:
    return isinstance(value, list)


def _is_dict(value: object) -> TypeIs[dict[object, object]]:
    return isinstance(value, dict)


def as_json(value: object) -> JSON:
    """`value` as JSON, checked all the way down. Raises TypeError for anything JSON can't hold."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if _is_list(value):
        return [as_json(item) for item in value]
    if _is_dict(value):
        return {str(key): as_json(item) for key, item in value.items()}
    raise TypeError(f"not a JSON value: {value!r}")


def loads(text: str | bytes) -> JSON:
    """json.loads, typed. Raises ValueError if the text isn't JSON."""
    return as_json(_decode(text))
