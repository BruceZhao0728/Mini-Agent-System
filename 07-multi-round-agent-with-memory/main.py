"""
Bare Agent - The simplest possible agent supporting reading and writing files
"""

import sys
import os
import time

from config import API_KEY, EXPORT_LOG, EXPORT_LOG_DIR, EXPORT_LOG_PATH
from config import CONTEXT_WARN_RATIO, CONTEXT_DANGER_RATIO
# Only USAGE_TEXT reads these four — it reports the real defaults rather than
# restating them, so the help cannot drift from config.py.
from config import MODEL, THINKING, BASE_URL, CONTEXT_WINDOW_TOKENS
from context import format_context_line
from react_agent import ReActAgent
from tools import FAILED
import memory

# Without readline, input() uses the terminal's canonical mode, and WSL's pty does not
# set the iutf8 flag by default: backspace then deletes *bytes*, so one press over a
# Chinese character (3 bytes in UTF-8) removes only the last one and leaves the rest
# behind. stdin decodes with surrogateescape, which preserves those orphans as \udcXX,
# and the next log write crashes the whole process (two real crashes on 2026-10-06,
# reproduced with a pty experiment). With `import readline`, input() switches to
# readline's own line editor: UTF-8 aware (one backspace deletes the whole character),
# and up-arrow history comes for free. On a non-tty (pipe, redirect) it degrades to the
# previous behaviour automatically.
try:
    import readline
except ImportError:
    pass

COMMANDS = ("/compact", "/context", "/help", "/memory", "/exit")

HELP_TEXT = """Available commands:
  /compact   Compress the context: summarize the earlier conversation into one message, keep the recent turns verbatim
  /context   Show the current context occupancy
  /memory    Show what long-term memory holds, and what it costs per request
  /help      Show this help
  /exit      Leave (you can also type exit / quit, or press Ctrl-D)


Anything else is sent to the agent as a prompt — including a slash-prefixed path such as `/etc/hosts`."""

# `--help` on the command line, as opposed to `/help` inside the REPL. The two are
# different questions ("how do I start this" vs "what can I type now"), so they are
# two texts; this one is printed before anything else runs and needs no API key.
USAGE_TEXT = f"""A ReAct agent with tools, a REPL and long-term memory.

Usage:
  python3 main.py                                  Multi-turn REPL, rooted at the cwd
  python3 main.py --root <dir>                     Same, but rooted at <dir>
  python3 main.py --msg '<prompt>'                 One-shot: run, stream the answer, exit
  python3 main.py --root <dir> --msg '<prompt>'    One-shot against <dir>
  python3 main.py --help                           This message

Options:
  --root <dir>    Make <dir> the workspace root. It must already exist.
  --msg <text>    The prompt; its presence means one-shot mode.
  -h, --help      This message. Needs no API key, creates no log file, changes nothing.
  --root=<dir> and --msg=<text> are accepted as well.

A bare argument (one not starting with -) is joined into the prompt, so the older form
`python3 main.py '<prompt>'` still works. Giving the prompt twice — as --msg and as a
bare argument — is an error rather than a guess.

<dir> becomes the process's working directory, so logs/, memory/ and tmp/ are created
inside it, execute_command runs there, and every tool path resolves against it —
read_file('src/app.py') means <dir>/src/app.py.

Environment:
  DEEPSEEK_API_KEY          required — the program exits if it is unset or still a placeholder
  DEEPSEEK_MODEL_NAME       default {MODEL}
  DEEPSEEK_THINKING         enabled | disabled            (default {THINKING})
  DEEPSEEK_BASE_URL         default {BASE_URL}
  DEEPSEEK_CONTEXT_WINDOW   token budget for the occupancy display (default {CONTEXT_WINDOW_TOKENS:,})

In the REPL, type /help for the command list (/compact, /context, /memory, /exit).

python3 _test.py runs the offline suite; it makes no API call."""


def show_context(agent, verbose: bool = False):
	'''Print the context occupancy line.

	The token numbers are the API's own usage report — the only source this
	design uses. The character count printed by /context is local, and exists
	only so a compaction can be compared before and after (COMPACT.md §7.1).
	'''
	stats = agent.context_stats()
	print(format_context_line(stats["used"], stats["window"], stats["messages"],
	                          CONTEXT_WARN_RATIO, CONTEXT_DANGER_RATIO))
	if verbose:
		print(f"         {stats['chars']:,} chars of history (a local character count, not a token count)")


def handle_command(agent, command: str) -> bool:
	'''Run one slash command. Returns True to leave the REPL.'''
	if command == "/exit":
		return True
	if command == "/help":
		print(HELP_TEXT)
	elif command == "/context":
		show_context(agent, verbose=True)
	elif command == "/compact":
		# verbose: print the process, the summary text and the resulting history. On
		# success compact() has already printed the result line itself; this branch
		# only has to print the explanation a failure returns.
		changed, message = agent.compact(verbose=True)
		if not changed:
			print(message)
			# The history did not move, so the last measurement still describes it
			show_context(agent)
	elif command == "/memory":
		print(memory.describe())
		# The index this session is using was built when the conversation started.
		# Notes written since then are on disk but not in the prompt — saying so is
		# the point of showing this at all, otherwise the two numbers look like a bug.
		# Before the first prompt reset() has not run, so nothing is injected yet;
		# without this branch the cold case reads as "0 chars" beside a non-empty store.
		if agent.messages:
			print(f"         {len(agent.memory_index):,} chars of memory index are in this"
				f" session's system prompt (a snapshot from when it started; notes written"
				f" since reach the index only in the next process)")
		else:
			print("         No session yet — the index is injected when the first prompt runs")
	return False


class _HelpRequested(Exception):
	'''Raised by parse_args when -h/--help appears, so help has one exit path.'''


def parse_args(argv: list) -> tuple:
	'''
	Split command-line arguments into (root, prompt). Either may be None.

	Signalling through exceptions is deliberate for a CLI parse: both outcomes have to
	abort *before* any side effect — no log file, no chdir — and a raised exception
	cannot be quietly ignored the way a returned error code can.

	Options are `--root <dir>` and `--msg <text>`, both also accepted as `--opt=value`.
	Any other argument not starting with "-" is a *bare* argument and is joined into the
	prompt. That is what keeps `python3 main.py '<prompt>'` working — the form every
	document in this repo uses — and it is unambiguous now that the root has a flag of
	its own: an earlier version guessed the root from "is argv[0] an existing directory",
	which turned `python3 main.py story` into a chdir into `story/`.

	Args:
		argv (list): sys.argv[1:].

	Returns:
		tuple: (root, prompt), each a str or None.

	Raises:
		_HelpRequested: -h/--help was passed.
		ValueError: unknown option, a missing/empty option value, or a prompt given twice.
	'''
	root = msg = None
	bare = []
	i = 0
	while i < len(argv):
		arg = argv[i]
		# --root=x / --msg=x; a bare -h has no "=" and keeps the whole token as its name.
		name, inline = arg.partition("=")[::2] if arg.startswith("--") and "=" in arg else (arg, None)

		if name in ("-h", "--help"):
			raise _HelpRequested
		if name in ("--root", "--msg"):
			if inline is None:
				if i + 1 >= len(argv):
					raise ValueError(f"{name} needs a value")
				inline = argv[i + 1]
				i += 1
			if not inline:
				raise ValueError(f"{name} was given an empty value")
			if name == "--root":
				root = inline
			else:
				msg = inline
		elif arg.startswith("-") and arg != "-":
			raise ValueError(f"unknown option: {arg} (try --help)")
		else:
			bare.append(arg)
		i += 1

	if msg is not None and bare:
		raise ValueError("the prompt was given twice — as --msg and as a bare argument")
	return root, msg if msg is not None else (" ".join(bare) or None)


# Entrance of main program

if __name__ == "__main__":
	try:
		root, prompt = parse_args(sys.argv[1:])
	except _HelpRequested:
		print(USAGE_TEXT)
		sys.exit(0)
	except ValueError as e:
		print(f"{e}\n")
		print(USAGE_TEXT)
		sys.exit(2)

	if root:
		try:
			os.chdir(root)
		except OSError as e:
			print(f"Cannot use {root!r} as the workspace root: {e}")
			sys.exit(1)
		# Say it out loud: every path below this point — logs/, memory/, tmp/, and every
		# argument the model passes to a tool — is relative to here, not to where you
		# typed the command. Silence would make that a guessing game.
		print(f"Workspace root: {os.getcwd()}")

	if API_KEY == "sk-your-key-here":
		print("Set DEEPSEEK_API_KEY or edit API_KEY in code")
		sys.exit(1)

	# A broken plugin must not take the agent down, but it must not disappear
	# silently either: tools/__init__.py records it in FAILED, and this is what consumes it.
	if FAILED:
		print(f"⚠ These tool plugins failed to load; their tools are unavailable: {FAILED}")

	if EXPORT_LOG:
		# Create export log directory if it doesn't exist
		if not os.path.exists(EXPORT_LOG_DIR):
			os.makedirs(EXPORT_LOG_DIR)

	agent = ReActAgent()

	# A prompt means one-shot mode: run once and exit. The answer is streamed inside
	# run(), so it is not printed a second time here.
	if prompt:
		print(f"Task: {prompt}")
		print("=" * 50)
		agent.run(prompt)
		sys.exit(0)

	# Without an argument = multi-turn conversation. The history lives only in memory
	# (agent.messages) and dies with the process — it is never written to disk and never
	# shared across processes. The log is a debug record, not the conversation's memory.
	print("Multi-turn mode: type your prompt, /help for commands, exit / quit / Ctrl-D to leave.")
	while True:
		try:
			prompt = input(f"\n[{agent.turn + 1}] you > ").strip()
		except (EOFError, KeyboardInterrupt):
			print()
			break
		if not prompt:
			continue
		if prompt in ("exit", "quit"):
			break

		# Commands are dispatched *before* the rollback snapshot below. The rollback
		# is an index slice (del agent.messages[start:]), while /compact rewrites the
		# whole list: when the two overlap, a shrinking compaction would make the
		# rollback silently swallow the interrupt, and a growing one (a summary can be
		# longer than what it replaces) would make it truncate the tail it meant to keep.
		words = prompt.split()
		if words[0] in COMMANDS:
			try:
				if handle_command(agent, words[0]):
					break
			except KeyboardInterrupt:
				print("\n(command interrupted — history untouched)")
			continue
		# Only a single slash-prefixed word counts as a mistyped command; a normal
		# prompt that happens to start with a slash (`/etc/hosts`) still goes to the model.
		if prompt.startswith("/") and len(words) == 1:
			print(f"Unknown command {words[0]} — type /help for the available commands")
			continue

		start = len(agent.messages)   # snapshot, so an interrupted turn can be rolled back
		start_turn = agent.turn
		try:
			agent.run(prompt)
		except KeyboardInterrupt:
			# The interrupt can land mid-turn — "assistant carries tool_calls, the tool
			# results are not written back yet" — and that history makes the next request
			# fail with a 400. Drop the whole turn and return to the last complete state.
			del agent.messages[start:]
			agent.turn = start_turn
			print("\n(turn interrupted — history rolled back)")
			show_context(agent)   # the size after the rollback is what this turn actually left behind
			continue
		show_context(agent)