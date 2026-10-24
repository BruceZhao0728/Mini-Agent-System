"""Local file and process tools."""
import os
import subprocess

from ._spec import tool
from ._common import _strip_ansi


@tool(
    description="Read a UTF-8 text file and return its contents.",
    parameters={
        "path": {"type": "string", "description": "Path to the file to read"},
    },
    required=["path"],
)
def read_file(path: str) -> str:
    '''
    Read a UTF-8 text file and return its contents.

    Text mode: a binary file (image / zip / PDF) raises UnicodeDecodeError while
    decoding. To get one of those, use download_file, which writes bytes.

    Args:
        path (str): The path to the file to read. "~" is expanded.

    Returns:
        str: The file's contents.
    '''
    path = os.path.expanduser(path)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


@tool(
    description="Write text to a file, creating parent directories if needed. Overwrites any existing content.",
    parameters={
        "path": {"type": "string", "description": "Path to the file to write"},
        "content": {"type": "string", "description": "Content to write"},
    },
    required=["path", "content"],
)
def write_file(path: str, content: str) -> str:
    '''
    Write text to a file, creating parent directories if needed.

    Text mode (UTF-8), and it overwrites existing content (it does not append).
    Binary goes through download_file, which downloads and then writes to disk.

    Args:
        path (str): The path to the file to write. "~" is expanded.
        content (str): The content to write to the file.

    Returns:
        str: "written: <path>".
    '''
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return f"written: {path}"


@tool(
    description=(
        "Execute a shell command and return its stdout and stderr. Times out after 30 seconds."
    ),
    parameters={
        "command": {"type": "string", "description": "Shell command to execute"},
    },
    required=["command"],
)
def execute_command(command: str) -> str:
    '''
    Execute a shell command and return its output.

    stdout and stderr are merged, ANSI escape sequences are stripped, and a
    non-zero exit code is appended. A command that fails silently (`false`)
    therefore returns "(no output; exit code 1)" instead of an empty string,
    which would be indistinguishable from success.

    Note that this does not catch the timeout itself: TimeoutExpired is in
    RETRIABLE_ERRORS, so one stuck command costs up to 1 + MAX_TOOL_RETRIES × 30s.
    Re-running a command with side effects means replaying them — run_python deliberately
    does not retry for exactly that reason, and this has not been aligned with it yet.
    That is a decision left to you.

    Args:
        command (str): The shell command to execute.

    Returns:
        str: The combined stdout and stderr, or an Error: message.
    '''
    result = subprocess.run(
        command, shell=True, capture_output=True, text=True,
        timeout=30, encoding="utf-8", errors="replace"
    )
    output = _strip_ansi(result.stdout + result.stderr)
    if result.returncode != 0:
        output += f"\n(exit code {result.returncode})"
    return output if output.strip() else f"(no output; exit code {result.returncode})"


@tool(
    description=(
        "List a directory's entries, one per line, each tagged [DIR] or [FILE]. Not recursive."
    ),
    parameters={
        "path": {"type": "string", "description": "Path to the directory, default '.'"},
    },
    required=[],
)
def list_directory(path: str = ".") -> str:
    '''
    List a directory's entries, one per line, each tagged [DIR] or [FILE].

    Those two tags are load-bearing: they are all the model has for telling "file" from
    "directory" (noted in 04/CLAUDE.md). os.listdir's raw order, unsorted; no cap on the
    number of entries, so a large directory has its middle clipped out by clip().

    Args:
        path (str): The directory to list. Defaults to the current directory.

    Returns:
        str: One entry per line, "[DIR] name" or "[FILE] name".
    '''
    path = os.path.expanduser(path)
    try:
        entries = os.listdir(path)
    except Exception as e:
        return f"Error listing directory: {e}"

    result = []
    for entry in entries:
        full_path = os.path.join(path, entry)
        if os.path.isdir(full_path):
            result.append(f"[DIR] {entry}")
        else:
            result.append(f"[FILE] {entry}")
    return "\n".join(result)
