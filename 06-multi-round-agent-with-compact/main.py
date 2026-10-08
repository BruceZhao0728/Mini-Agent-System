"""
Bare Agent - The simplest possible agent supporting reading and writing files
"""

import sys
import os
import time

from config import API_KEY, EXPORT_LOG, EXPORT_LOG_DIR, EXPORT_LOG_PATH
from config import CONTEXT_WARN_RATIO, CONTEXT_DANGER_RATIO
from context import format_context_line
from react_agent import ReActAgent
from tools import FAILED

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

COMMANDS = ("/compact", "/context", "/help", "/exit")

HELP_TEXT = """Available commands:
  /compact   Compress the context: summarize the earlier conversation into one message, keep the recent turns verbatim
  /context   Show the current context occupancy
  /help      Show this help
  /exit      Leave (you can also type exit / quit, or press Ctrl-D)

Anything else is sent to the agent as a prompt — including a slash-prefixed path such as `/etc/hosts`."""


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
	return False


# Entrance of main program

if __name__ == "__main__":
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

	# With an argument = one-shot mode: run once and exit. The answer is streamed inside
	# run(), so it is not printed a second time here.
	if len(sys.argv) >= 2:
		prompt = sys.argv[1]
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