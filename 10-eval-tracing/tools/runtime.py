"""Code execution tool."""
import os
import subprocess
import sys
import time

from ._spec import tool
from ._common import _strip_ansi


@tool(
    description=(
        "Run a Python 3 snippet in a fresh subprocess and return its stdout and stderr. "
        "Use this for calculation, data processing and trying library calls. "
        "Variables do NOT persist between calls."
    ),
    parameters={
        "code": {"type": "string", "description": "Python source to execute"},
        "timeout": {"type": "integer", "description": "Seconds to allow, default 30"},
    },
    required=["code"],
)
def run_python(code: str, timeout: int = 30) -> str:
    '''
    Run a Python 3 snippet in a fresh subprocess and return its output.

    stdout and stderr are merged, ANSI escape sequences are stripped, and a
    non-zero exit code is appended — same tail as execute_command.

    Notes:
    - The interpreter is sys.executable: the same one as the agent, so any package
        the agent can import, the snippet can import too.
    - Every call leaves a tmp/agent_py_*.py behind and never cleans up. Many calls
        pile up.
    - A timeout returns a string instead of raising TimeoutExpired, a deliberate
        departure from 03's convention: TimeoutExpired is in RETRIABLE_ERRORS, so
        copying the convention would auto-rerun a timed-out snippet 3 times — that is,
        replay its side effects. execute_command has not been aligned with this yet.

    Args:
        code (str): Python source to execute. Written to tmp/ first.
        timeout (int): Seconds to allow.

    Returns:
        str: Combined stdout and stderr, or an Error: message.
    '''
    os.makedirs("tmp", exist_ok=True)
    path = os.path.join("tmp", f"agent_py_{int(time.time() * 1000)}.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)

    env = dict(os.environ, NO_COLOR="1", PYTHON_COLORS="0", TERM="dumb")
    try:
        result = subprocess.run([sys.executable, path], capture_output=True,
                                text=True, timeout=timeout, env=env, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return (f"Error: python snippet timed out after {timeout}s and was not retried "
                f"(re-running would repeat its side effects). Source kept at {path}.")

    output = _strip_ansi(result.stdout + result.stderr)
    if result.returncode != 0:
        output += f"\n(exit code {result.returncode})"
    return output if output.strip() else f"(no output; exit code {result.returncode})"
