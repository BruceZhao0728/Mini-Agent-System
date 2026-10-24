"""Version-control tools (read-only whitelist: status / diff / log)."""
import os
import subprocess

from ._spec import tool
from ._common import _strip_ansi


def _run_git(path: str, args: list, timeout: int = 30) -> str:
    '''
    Run one git subcommand against an arbitrary repository path.

    Note that this function isn't a tool, but a private helper shared by these three git tools.
    Hence it doesn't require a decorator and shouldn't be imported by other tools.

    Args:
        path (str): The repository to operate on ("." for the current directory).
        args (list): git arguments, e.g. ["status", "--short"].
        timeout (int): Seconds to allow.

    Returns:
        str: git's stdout, or an "Error: ..." line describing the failure.
    '''

    path = os.path.expanduser(path)

    # --no-pager: otherwise it can hang waiting for a TTY
    # core.quotepath=false: otherwise Chinese filenames become \346\226\207
    # color.ui=false: not enough (a color.status=always in the user's gitconfig wins),
    #                 so the output gets another _strip_ansi pass
    cmd = ["git", "-C", path, "--no-pager",
           "-c", "core.quotepath=false", "-c", "color.ui=false"] + args
    env = dict(os.environ, GIT_PAGER="cat", GIT_TERMINAL_PROMPT="0", NO_COLOR="1")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=timeout, env=env)
    except FileNotFoundError:
        return "Error: the git executable was not found on PATH"
    except subprocess.TimeoutExpired:
        return f"Error: git {' '.join(args)} timed out after {timeout}s in {path}"

    # git reports failure entirely through stderr + a non-zero exit code. Without the
    # returncode check the model gets an empty string, which looks like "the repo is clean".
    if result.returncode != 0:
        detail = _strip_ansi(result.stderr or result.stdout).strip()
        return f"Error: git {' '.join(args)} failed (exit {result.returncode}): {detail}"

    out = _strip_ansi(result.stdout).strip()
    return out if out else "(no output - nothing to report)"


@tool(
    description=(
        "Show the working-tree status of a git repository: branch plus changed files. "
        "Path can be any directory inside the repo."
    ),
    parameters={
        "path": {"type": "string", "description": "Path to the repository, default '.'"},
    },
    required=[],
)
def git_status(path: str = ".") -> str:
    '''
    Show the working-tree status of a git repository: branch plus changed files.

    Args:
        path (str): Path to the repository (any directory inside it works).
            Defaults to the current directory. "~" is expanded.

    Returns:
        str: `git status --short --branch` output, or an Error: message.
    '''
    path = os.path.expanduser(path)
    return _run_git(path, ["status", "--short", "--branch"])


@tool(
    description=(
        "Show changes in a git repository. Path can be any directory inside the repo. "
        "'staged' diffs the index against HEAD instead of the working tree. "
        "'target' is an optional revision to diff against, e.g. 'HEAD~1'."
    ),
    parameters={
        "path": {"type": "string", "description": "Path to the repository, default '.'"},
        "staged": {"type": "boolean", "description": "Diff the staged index instead of the working tree"},
        "target": {"type": "string", "description": "Revision to diff against, e.g. HEAD~1"},
    },
    required=[],
)
def git_diff(path: str = ".", staged: bool = False, target: str = "") -> str:
    '''
    Show changes in a git repository.

    Note that the output may exceed MAX_OBS_CHARS and clip() will remove the middle part.
    Hence the so-called "complete diff" seen by the model may be incomplete.
    You may first apply git_status to determine the scale, or apply this function to small differences.
    You may also set target="--stat" for a terse format (git treats target as an option).

    Args:
        path (str): Path to the repository. Defaults to the current directory.
        staged (bool): Diff the index against HEAD instead of the working tree.
        target (str): Optional revision to diff against, e.g. "HEAD~1".

    Returns:
        str: The diff, or an Error: message.
    '''
    path = os.path.expanduser(path)
    args = ["diff"]
    if staged:
        args.append("--cached")
    if target:
        args.append(target)
    return _run_git(path, args)


@tool(
    description=(
        "Show recent commits of a git repository, one compact line per commit: <hash> <date> <author> <subject>. "
        "'limit' controls how many (default 20). Path can be any directory inside the repo."
    ),
    parameters={
        "path": {"type": "string", "description": "Path to the repository, default '.'"},
        "limit": {"type": "integer", "description": "How many commits to show, default 20"},
    },
    required=[],
)
def git_log(path: str = ".", limit: int = 20) -> str:
    '''
    Show recent commits of a git repository, one compact line per commit.

    Args:
        path (str): Path to the repository. Defaults to the current directory.
        limit (int): How many commits to show (most recent first).

    Returns:
        str: "<hash> <date> <author> <subject>" lines, or an Error: message.
    '''
    path = os.path.expanduser(path)
    return _run_git(path, ["log", "-n", str(limit), "--date=short", "--pretty=format:%h %ad %an %s"])
