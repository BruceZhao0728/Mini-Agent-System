"""Retry behavior, tool argument parsing, and plugin loading assertions."""

import os, shutil, subprocess, sys, tempfile, time
from types import SimpleNamespace
from agent import ReActAgent
from tools import TOOLS
from config import MAX_TOOL_RETRIES

a = ReActAgent()

calls = {"n": 0}
def flaky():
    """Perform the flaky operation. Returns: The result of this operation."""
    calls["n"] += 1
    if calls["n"] < 3:                       # Test invariant or expected behavior.
        raise subprocess.TimeoutExpired("cmd", 30)
    return "ok"
TOOLS["flaky"] = flaky

t0 = time.time()
out = a._call_tool("flaky", {})
dt = time.time() - t0
print(f"1. {out!r} calls={calls['n']} {dt:.2f}s")
assert out == "ok", out
assert calls["n"] == 3, calls["n"]
assert 1.4 <= dt <= 3.0, f"退避时长 {dt:.2f}s 不在 [1.4, 3.0]"

# Test invariant or expected behavior.
# Test invariant or expected behavior.
calls2 = {"n": 0}
def deterministic():
    """Perform the deterministic operation. Returns: The result of this operation."""
    calls2["n"] += 1
    raise FileNotFoundError("nope.md")
TOOLS["deterministic"] = deterministic

t0 = time.time()
out = a._call_tool("deterministic", {})
dt = time.time() - t0
print(f"2. {out!r} calls={calls2['n']} {dt:.2f}s")
assert "FileNotFoundError" in out, out        # Test invariant or expected behavior.
assert calls2["n"] == 1, f"确定性错误被重试了 {calls2['n']} 次"
assert dt < 0.5, dt

# Test invariant or expected behavior.
# Test invariant or expected behavior.
calls3 = {"n": 0}
def always_timeout():
    """Perform the always timeout operation. Returns: The result of this operation."""
    calls3["n"] += 1
    raise subprocess.TimeoutExpired("cmd", 30)
TOOLS["always_timeout"] = always_timeout

t0 = time.time()
out = a._call_tool("always_timeout", {})
dt = time.time() - t0
print(f"3. {out!r} calls={calls3['n']} {dt:.2f}s")
assert calls3["n"] == MAX_TOOL_RETRIES + 1, calls3["n"]
assert "TimeoutExpired" in out, out
assert 3.2 <= dt <= 6.0, f"总退避 {dt:.2f}s 不在 [3.2, 6.0]"

# Test invariant or expected behavior.
# Test invariant or expected behavior.
def tc(args):   # Test invariant or expected behavior.
    """Perform the tc operation. Args: args: Value used by this operation. Returns: The result of this operation."""
    return SimpleNamespace(function=SimpleNamespace(name="read_file", arguments=args))

cases = [
    # Test invariant or expected behavior.
    ('{path: "x"}',               {},                        "Invalid JSON"),
    ('',                          {},                        ""),              # Test invariant or expected behavior.
    ('[1,2]',                     {},                        "JSON object"),   # Test invariant or expected behavior.
    ('3',                         {},                        "JSON object"),
    ('{"path": "/etc/hostname"}', {"path": "/etc/hostname"}, ""),
]
for raw, exp_args, exp_frag in cases:
    args, err = a._parse_args(tc(raw))
    ok = args == exp_args and exp_frag in err
    print(f"4. {raw!r:28} -> ({args}, {err[:48]!r})  {'ok' if ok else 'FAIL'}")
    assert ok, f"输入 {raw!r}：args={args} err={err!r}"

# Test invariant or expected behavior.
# Test invariant or expected behavior.
# Test invariant or expected behavior.
#
# Test invariant or expected behavior.
# Test invariant or expected behavior.
TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")


def _patch(pkg, rel, old, new):
    """Perform the  patch operation. Args: pkg: Value used by this operation; rel: Value used by this operation; old: Value used by this operation; new: Value used by this operation. Returns: The result of this operation."""
    p = os.path.join(pkg, rel)
    s = open(p, encoding="utf-8").read()
    assert old in s, f"注入锚点已失效，这组测试需要跟着改: {rel} / {old[:60]!r}"
    open(p, "w", encoding="utf-8").write(s.replace(old, new, 1))


def _none(pkg):
    """Perform the  none operation. Args: pkg: Value used by this operation. Returns: The result of this operation."""
    pass
def _dup_name(pkg):
    # Test invariant or expected behavior.
    """Perform the  dup name operation. Args: pkg: Value used by this operation. Returns: The result of this operation."""
    _patch(pkg, "local.py", 'required=["path"],\n)\ndef read_file',
                          'required=["path"],\n    name="list_directory",\n)\ndef read_file')
def _bad_required(pkg):
    """Perform the  bad required operation. Args: pkg: Value used by this operation. Returns: The result of this operation."""
    _patch(pkg, "local.py", 'required=["path"],', 'required=["path","nope"],')
def _undef_in_tool(pkg):
    # Test invariant or expected behavior.
    """Perform the  undef in tool operation. Args: pkg: Value used by this operation. Returns: The result of this operation."""
    _patch(pkg, "local.py", "import os\nimport subprocess\n", "import subprocess\n")
def _undef_in_helper(pkg):
    # Test invariant or expected behavior.
    # Test invariant or expected behavior.
    """Perform the  undef in helper operation. Args: pkg: Value used by this operation. Returns: The result of this operation."""
    _patch(pkg, "vcs.py", "import os\nimport subprocess\n", "import os\n")
def _no_return(pkg):
    """Perform the  no return operation. Args: pkg: Value used by this operation. Returns: The result of this operation."""
    _patch(pkg, "_spec.py", "        return fn\n    return deco", "        pass\n    return deco")
def _broken_plugin(pkg):
    """Perform the  broken plugin operation. Args: pkg: Value used by this operation. Returns: The result of this operation."""
    open(os.path.join(pkg, "vcs.py"), "w", encoding="utf-8").write("@tool(\n")


def load_with(inject):
    """Copy the tools package, inject a fault, import it in a child process, and return (exit code, output)."""
    with tempfile.TemporaryDirectory() as d:
        pkg = os.path.join(d, "tools")
        shutil.copytree(TOOLS_DIR, pkg, ignore=shutil.ignore_patterns("__pycache__"))
        inject(pkg)
        r = subprocess.run([sys.executable, "-c",
                            "import tools; print('LOADED', len(tools.TOOLS), tools.FAILED)"],
                           cwd=d, capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr


loader_cases = [
    # Test invariant or expected behavior.
    # Test invariant or expected behavior.
    # Test invariant or expected behavior.
    # Test invariant or expected behavior.
    ("原样加载（不该报错）",             _none,            0,        "LOADED"),
    ("插件整个坏掉（降级，不该 raise）",  _broken_plugin,   0,        "'vcs'"),
    ("工具名重复（会静默覆盖）",         _dup_name,        1,        "duplicate tool name(s)"),
    ("required 不在 properties 里",      _bad_required,    1,        "absent from properties"),
    ("工具函数引用未定义的名字",         _undef_in_tool,   1,        "references undefined name(s)"),
    ("helper 引用未定义的名字",          _undef_in_helper, 1,        "references undefined name(s)"),
    ("deco 忘了 return fn",              _no_return,       1,        "forget to return fn"),
]
for desc, inject, exp_rc, frag in loader_cases:
    rc, out = load_with(inject)
    ok = rc == exp_rc and frag in out
    print(f"5. {desc:30} rc={rc} (期望 {exp_rc})  {'ok' if ok else 'FAIL'}")
    if not ok:
        print("   " + out.strip().replace("\n", "\n   ")[-400:])
    assert ok, f"{desc}: rc={rc} 期望 {exp_rc}；输出里没找到 {frag!r}"

# Test invariant or expected behavior.
# Test invariant or expected behavior.
# Test invariant or expected behavior.
# Test invariant or expected behavior.
