"""Terminal width, ANSI handling, and rendering environment helpers."""

import os
import re
import sys
import unicodedata
from config import RENDER_MARKDOWN

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_RESET = "\x1b[0m"

def strip_ansi(text: str) -> str:
	'''Drop SGR sequences — everything this module emits is SGR.'''
	return _ANSI_RE.sub("", str(text))


def display_width(text: str) -> int:
	'''How many terminal columns `text` occupies.

	ANSI sequences count 0 (they are styling, not content); East Asian
	Wide/Fullwidth characters count 2. Ambiguous-width characters count 1,
	which is what most terminals do. This is the function that makes a table of
	Chinese text line up.
	'''
	plain = strip_ansi(text)
	total = 0
	for c in plain:
		if unicodedata.combining(c):		# accents ride on the previous column
			continue
		total += 2 if unicodedata.east_asian_width(c) in "WF" else 1
	return total


def rendering_enabled() -> bool:
	'''Interactive rendering is on: stdout is a tty, config.RENDER_MARKDOWN is on,
	NO_COLOR is unset.'''
	return RENDER_MARKDOWN and not os.environ.get("NO_COLOR") and sys.stdout.isatty()
