"""Binds a tool function and its schema into one declaration — written once per tool."""

_REGISTRY = []


def tool(description, parameters, required=(), name=None):
    '''
    Usage:

        @tool(description="Read content from a file",
              parameters={"path": {"type": "string"}},
              required=["path"])
        def read_file(path: str) -> str:
            ...

    name defaults to the function name — so the name used for dispatch and the
    name sent to the model cannot disagree.
    '''
    def deco(fn):
        """Perform the deco operation. Args: fn: Value used by this operation. Returns: The result of this operation."""
        _REGISTRY.append({
            "name": name or fn.__name__,
            "fn": fn,
            "description": description,
            "parameters": parameters,
            "required": list(required),
        })
        return fn
    return deco


def registry():
    """Perform the registry operation. Returns: The result of this operation."""
    return list(_REGISTRY)
