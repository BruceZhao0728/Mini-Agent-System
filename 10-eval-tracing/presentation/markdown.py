"""Block-aware Markdown rendering, including incremental streamed output."""

import re
from .inline import _inline, _math_span
from .tables import _is_table_row, _is_separator_row, _split_row, _render_table
from .terminal import rendering_enabled

def _render_line(line: str, in_code: bool, in_math: bool) -> tuple[str, bool, bool]:
	'''Render one markdown line; returns (rendered, in_code, in_math) for the next line.'''
	stripped = line.lstrip()

	if in_code:
		if stripped.startswith("```"):
			return "\x1b[2m┄┄\x1b[22m", False, in_math
		return f"\x1b[2m{line}\x1b[22m", True, in_math

	if in_math:
		if stripped == "$$":
			return "", in_code, False
		return _math_span(line), in_code, True

	if stripped.startswith("```"):
		lang = stripped[3:].strip()
		return f"\x1b[2m┄┄{(' ' + lang) if lang else ''}\x1b[22m", True, in_math

	if stripped.startswith("$$"):
		body = stripped[2:].strip()
		if body.endswith("$$"):
			return _math_span(body[:-2]), in_code, in_math     # $$...$$ on one line
		if not body:
			return "", in_code, True                            # bare $$ opens a block
		return _math_span(body), in_code, True

	m = re.match(r"^(\s{0,3})(#{1,6})\s+(.*)$", line)
	if m:
		open_codes = "\x1b[1m" + ("\x1b[4m" if len(m.group(2)) == 1 else "")
		return f"{m.group(1)}{open_codes}{_inline(m.group(3))}\x1b[24m\x1b[22m", False, in_math

	if re.match(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$", line):
		return "\x1b[2m" + "─" * 30 + "\x1b[22m", False, in_math

	m = re.match(r"^(\s{0,3})>\s?(.*)$", line)
	if m:
		return f"\x1b[34m▎\x1b[39m {_inline(m.group(2))}", False, in_math

	m = re.match(r"^(\s*)([-*+]|\d{1,3}\.)\s+(.*)$", line)
	if m:
		return f"{m.group(1)}\x1b[36m{m.group(2)}\x1b[39m {_inline(m.group(3))}", False, in_math

	return _inline(line), False, in_math


class MarkdownStream:
	'''Incremental line renderer for streamed output.

	feed(chunk) returns the rendered text of every complete line seen so far
	("" when the chunk only extends the current partial line); flush() renders
	the trailing partial line at the end of the stream. Fence state is kept
	across calls, so a code block spanning chunks still renders correctly.

	Two states hold lines *back* rather than rendering them immediately:

	  * a line that might be a table header waits one line, because only the
	    next line (the |---| separator) can say whether it is one;
	  * a confirmed table waits for the block to end, because every row is
	    needed before any column width is known.

	Both are flushed at the latest by flush(), so nothing is ever lost — the
	cost is that a table appears once its last row arrives, not row by row.
	'''

	def __init__(self):
		"""Perform the   init   operation. Returns: The result of this operation."""
		self._buf = ""
		self._in_code = False
		self._in_math = False
		self._pending = None	# a line held back in case it is a table header
		self._table = None		# confirmed table rows, held until the block ends
		self._ncols = 0

	def _emit(self, line: str) -> str:
		"""Perform the  emit operation. Args: line: Value used by this operation. Returns: The result of this operation."""
		rendered, self._in_code, self._in_math = _render_line(line, self._in_code, self._in_math)
		return rendered

	def _consume(self, line: str) -> list:
		'''Render one line. Returns a list of rendered lines — empty means the
		line was held back, and a table block returns several lines at once.

		A list, not a string, because a flushed table expands to many lines and
		the caller's newline bookkeeping assumes one entry per output line.
		Returning a pre-joined multi-line string here made `feed` add a newline
		on top of the block's own, which desynchronised streaming from
		whole-text rendering by exactly one blank line.
		'''
		if self._in_code or self._in_math:
			return [self._emit(line)]

		if self._table is not None:
			if _is_table_row(line) and (line.strip().startswith("|")
										or len(_split_row(line)) == self._ncols):
				self._table.append(line)
				return []
			block = _render_table(self._table).split("\n")
			self._table = None
			return block + self._consume(line)

		if self._pending is not None:
			held, self._pending = self._pending, None
			if _is_separator_row(line) and len(_split_row(line)) == len(_split_row(held)):
				self._table = [held, line]
				self._ncols = len(_split_row(held))
				return []
			return [self._emit(held)] + self._consume(line)

		if _is_table_row(line):
			self._pending = line
			return []

		return [self._emit(line)]

	def feed(self, chunk: str) -> str:
		"""Perform the feed operation. Args: chunk: Value used by this operation. Returns: The result of this operation."""
		self._buf += str(chunk)
		out = []
		while "\n" in self._buf:
			line, self._buf = self._buf.split("\n", 1)
			out.extend(self._consume(line))
		return ("\n".join(out) + "\n") if out else ""

	def flush(self) -> str:
		"""Perform the flush operation. Returns: The result of this operation."""
		out = []
		# The partial line first. It is the tail of the stream and may itself be
		# a table row, which has to join the open table *before* that table is
		# rendered — flush the table first and the last row is emitted raw.
		if self._buf:
			out.extend(self._consume(self._buf))
			self._buf = ""
		if self._pending is not None:
			# Emitted directly, not through _consume: there is no more input to
			# wait for, so a lone row is just a line — and _consume would hold
			# it back as a table header all over again.
			out.append(self._emit(self._pending))
			self._pending = None
		if self._table is not None:
			out.extend(_render_table(self._table).split("\n"))
			self._table = None
		return "\n".join(out)


def markdown_to_ansi(text: str) -> str:
	'''Pure function: markdown subset → ANSI codes. No tty/config checks — see render_markdown().'''
	stream = MarkdownStream()
	out = []
	for line in str(text).split("\n"):
		out.extend(stream._consume(line))
	tail = stream.flush()
	if tail:
		out.extend(tail.split("\n"))
	return "\n".join(out)


def render_markdown(text: str) -> str:
	'''Render a whole markdown text for the terminal — the non-streaming variant of
	MarkdownStream; a no-op when rendering is disabled.'''
	if not rendering_enabled():
		return str(text)
	return markdown_to_ansi(text)
