"""
Bare Agent - The simplest possible agent supporting reading and writing files
"""

import sys
import os

from config import API_KEY, EXPORT_LOG, EXPORT_LOG_DIR, EXPORT_LOG_PATH
from react_agent import ReActAgent

# Entrance of main program

if __name__ == "__main__":
	if len(sys.argv) < 2:
		print(f"Usage: python {sys.argv[0]} 'your prompt'")
		sys.exit(1)

	if API_KEY == "sk-your-key-here":
		print("Set DEEPSEEK_API_KEY or edit API_KEY in code")
		sys.exit(1)

	prompt = sys.argv[1]
	print(f"Task: {prompt}")
	print("=" * 50)

	if EXPORT_LOG:
		# Create export log directory if it doesn't exist
		if not os.path.exists(EXPORT_LOG_DIR):
			os.makedirs(EXPORT_LOG_DIR)
		# Clear log
		with open(EXPORT_LOG_PATH, "w") as f:
			f.write("")

	result = ReActAgent().run(prompt)
	print("\n" + "=" * 50)
	print(f"Result: {result}")