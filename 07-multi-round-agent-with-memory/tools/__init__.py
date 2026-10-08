"""The tool autoloader.

Every tools/*.py is a plugin; functions declared with @tool are collected automatically.
react_agent.py and _test.py need no changes at all — all they care about are TOOLS and TOOLS_DESC.

Load time does two things:
  1. Discover and import each plugin (a leading underscore means shared code, not a plugin)
  2. Validate — pull author errors that would otherwise surface at runtime as baffling
     symptoms forward into a failure at startup
"""
import ast as _ast
import builtins as _builtins
import importlib
import inspect as _inspect
import pkgutil
import symtable as _symtable
import sys as _sys
import textwrap as _textwrap

from ._spec import registry


def _undefined_globals(mod):
    '''
    Find names that a plugin's top-level functions reference but that the module
    neither defines nor finds among the builtins.

    Why this exists: once the code is split into plugins every file has to carry its own
    imports, and **a missing one does not raise at import time** — a function body only
    runs when it is called. Without this check the agent starts up perfectly normally and
    only blows up with a NameError the first time the model calls that tool.

    It scans **every top-level function** in the plugin, not just the tool functions: a
    helper (vcs.py's _run_git, say) with a missing import breaks its tools the same way
    when it is called, and the helper itself is not a tool.

    Args:
        mod: An already-imported plugin module.

    Returns:
        dict: {function name: [undefined names]}, an empty dict when nothing is wrong.
    '''
    try:
        whole = _inspect.getsource(mod)
    except (OSError, TypeError):
        return {}                      # no source (frozen / exec): skip rather than false-alarm

    names = set(vars(mod))
    problems = {}
    for node in _ast.parse(whole).body:
        if not isinstance(node, _ast.FunctionDef):
            continue
        src = _ast.get_source_segment(whole, node)   # note: decorators are not included
        if not src:
            continue
        bad = set()

        def walk(table):
            for sym in table.get_symbols():
                n = sym.get_name()
                if (sym.is_global() and not sym.is_assigned()
                        and n not in names and not hasattr(_builtins, n)):
                    bad.add(n)
            for child in table.get_children():
                walk(child)

        walk(_symtable.symtable(_textwrap.dedent(src), "<plugin>", "exec"))
        if bad:
            problems[node.name] = sorted(bad)
    return problems


# A failed plugin must not take the whole agent down, but it must not vanish silently
# either. Record it so main.py can print it.
FAILED = {}

for _m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
    if _m.name.startswith("_"):        # _spec / _common are shared code, not plugins
        continue
    try:
        importlib.import_module(f"{__name__}.{_m.name}")
    except Exception as e:
        FAILED[_m.name] = f"{type(e).__name__}: {e}"

_tools = registry()


# ---------------------------------------------------------------------------
# Load-time validation
#
# Everything raised here is an **author error**: deterministic, fixed by editing the
# code. It would otherwise surface only at runtime, as "this tool is a bit flaky", so
# it is pulled forward into a failure at startup. Environment problems (a plugin that
# will not import) go to FAILED above — degraded but recorded, never raised.
# ---------------------------------------------------------------------------

# 1) No top-level function in a plugin may reference a name that does not exist
#    (usually a missing import). This runs first: when it trips, every check below
#    blows up with a NameError and the error is very hard to read.
for _mod_name in sorted({t["fn"].__module__ for t in _tools}):
    _mod = _sys.modules[_mod_name]
    for _fn_name, _bad in sorted(_undefined_globals(_mod).items()):
        raise RuntimeError(
            f"{_mod.__name__}.{_fn_name}() references undefined name(s) {_bad} — "
            f"is this plugin missing an import?")

# 2) Tool names must be unique: TOOLS is a dict, so the later one silently wins
_all_names = [t["name"] for t in _tools]
_dupes = sorted({n for n in _all_names if _all_names.count(n) > 1})
if _dupes:
    raise RuntimeError(
        f"duplicate tool name(s) {_dupes} — two plugins declared the same name, "
        f"and the one loaded later silently overwrites the earlier")

# 3) Every tool's declaration must be self-consistent
for _t in _tools:
    _mod = _sys.modules[_t["fn"].__module__]
    _fn_name = _t["fn"].__name__

    # This one catches a "@tool deco that forgot to return fn": the registry still holds
    # the real function (the tool works as usual), but the plugin's module attribute was
    # overwritten by the return value, so tools.xxx becomes None.
    # Note: testing callable(_t["fn"]) alone is not enough — that is always True, dead code.
    _attr = getattr(_mod, _fn_name, None)
    if _attr is not _t["fn"]:
        raise RuntimeError(
            f"{_t['name']}: {_mod.__name__}.{_fn_name} is not that function "
            f"(it is {_attr!r}) — did the @tool deco forget to return fn?")

    _missing = set(_t["required"]) - set(_t["parameters"])
    if _missing:
        raise RuntimeError(
            f"{_t['name']}: required lists parameter(s) absent from properties "
            f"{sorted(_missing)} — the model cannot supply that argument")


TOOLS = {t["name"]: t["fn"] for t in _tools}

# Also hang the functions on the package namespace: tools.read_file(...) still works.
# setdefault — never overwrite this module's own TOOLS / TOOLS_DESC / FAILED.
for _n, _f in TOOLS.items():
    globals().setdefault(_n, _f)

TOOLS_DESC = [
    {"type": "function",
     "function": {"name": t["name"],
                  "description": t["description"],
                  "parameters": {"type": "object",
                                 "properties": t["parameters"],
                                 "required": t["required"]}}}
    for t in _tools
]
