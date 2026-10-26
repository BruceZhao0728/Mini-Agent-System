"""Binds a tool function and its schema into one declaration — written once per tool."""

_REGISTRY = []


def tool(description, parameters, required=(), name=None, parallel_safe=False):
    '''
    Usage:

        @tool(description="Read content from a file",
              parameters={"path": {"type": "string"}},
              required=["path"],
              parallel_safe=True)
        def read_file(path: str) -> str:
            ...

    name defaults to the function name — so the name used for dispatch and the
    name sent to the model cannot disagree. parallel_safe is client-side
    scheduling policy; the loader validates it and it never enters TOOLS_DESC.
    '''
    def deco(fn):
        _REGISTRY.append({
            "name": name or fn.__name__,
            "fn": fn,
            "description": description,
            "parameters": parameters,
            "required": list(required),
            "parallel_safe": parallel_safe,
        })
        return fn
    return deco


def registry():
    return list(_REGISTRY)
