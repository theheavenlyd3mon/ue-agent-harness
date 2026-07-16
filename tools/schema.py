"""Generate OpenAI-style tool schemas from Python callables."""

import inspect
from typing import Callable


TYPES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _json_type(t: type) -> str:
    if t in TYPES:
        return TYPES[t]
    origin = getattr(t, "__origin__", None)
    if origin is list or (isinstance(t, type) and issubclass(t, list)):
        return "array"
    if origin is dict or (isinstance(t, type) and issubclass(t, dict)):
        return "object"
    return "string"


def schema_for(fn: Callable) -> dict:
    sig = inspect.signature(fn)
    doc = (fn.__doc__ or "").strip()
    properties = {}
    required = []
    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        param_type = _json_type(param.annotation if param.annotation is not inspect.Parameter.empty else str)
        properties[name] = {
            "type": param_type,
            "description": f"Parameter `{name}` for `{fn.__name__}`.",
        }
        if param.default is inspect.Parameter.empty:
            required.append(name)
    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": doc,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def schemas_from_registry(registry: dict[str, Callable]) -> list[dict]:
    return [schema_for(fn) for fn in registry.values()]
