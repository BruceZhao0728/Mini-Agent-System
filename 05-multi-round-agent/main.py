"""
Bare Agent - The simplest possible agent supporting reading and writing files
"""

import sys
import os
import time

from config import API_KEY, EXPORT_LOG, EXPORT_LOG_DIR, EXPORT_LOG_PATH
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
	print("Multi-turn mode: type your prompt, or exit / quit / Ctrl-D to leave.")
	while True:
		try:
			prompt = input(f"\n[{agent.turn + 1}] you > ").strip()
		except (EOFError, KeyboardInterrupt):
			print()
			break
		if not prompt:
			continue
		if prompt in ("exit", "quit", "/exit"):
			break

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
			continue