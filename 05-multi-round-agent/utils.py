import os
import re
import sys

from config import MAX_OBS_CHARS, RENDER_MARKDOWN

def clip(text: str, head: int = 6000, tail: int = 1500) -> str:
	'''
	Clip the output of tools that go into history to a limited length: keep the head and tail, and omit the middle.
	
	Args:
		text (str): The text to clip.
		head (int): The number of characters to keep from the start of the text. Default is 6000.
		tail (int): The number of characters to keep from the end of the text. Default is 1500.
	
	Returns:
		str: The clipped text, with the middle omitted if it exceeds MAX_OBS_CHARS
	'''
	s = str(text)
	if len(s) <= MAX_OBS_CHARS:
		return s
	return f"{s[:head]}\n…[Clipped {len(s) - head - tail} characters]…\n{s[-tail:]}"

# ---------------------------------------------------------------------------
# Markdown → ANSI: render a subset of markdown as terminal styling.
# Display-only — messages, the API payload and the log always keep the raw
# markdown; only the prints that a human reads go through render_markdown().
# ---------------------------------------------------------------------------

def _inline(text: str) -> str:
	'''Render inline markdown: code spans, emphasis, strikethrough, links.'''
	# Stash code spans first so * and _ inside them are not treated as emphasis.
	codes = []
	def _stash(m):
		codes.append(m.group(1))
		return f"\x00{len(codes) - 1}\x00"
	text = re.sub(r"`([^`]+)`", _stash, text)

	text = re.sub(r"\*\*\*(.+?)\*\*\*", lambda m: f"\x1b[1;3m{m.group(1)}\x1b[22m\x1b[23m", text)
	text = re.sub(r"\*\*(.+?)\*\*", lambda m: f"\x1b[1m{m.group(1)}\x1b[22m", text)
	# ASCII-only word boundaries: CJK text has no spaces around emphasis, so \w
	# lookarounds would wrongly block 他说*很好*啊 (and would italicise 2*3*4).
	text = re.sub(r"(?<![A-Za-z0-9*])\*([^*\n]+?)\*(?![A-Za-z0-9*])", lambda m: f"\x1b[3m{m.group(1)}\x1b[23m", text)
	text = re.sub(r"(?<![A-Za-z0-9_])_([^_\n]+?)_(?![A-Za-z0-9_])", lambda m: f"\x1b[3m{m.group(1)}\x1b[23m", text)
	text = re.sub(r"~~(.+?)~~", lambda m: f"\x1b[9m{m.group(1)}\x1b[29m", text)
	text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: f"\x1b[4m{m.group(1)}\x1b[24m\x1b[2m ({m.group(2)})\x1b[22m", text)

	text = re.sub(r"\x00(\d+)\x00", lambda m: f"\x1b[36m{codes[int(m.group(1))]}\x1b[39m", text)
	return text


def _render_line(line: str, in_code: bool) -> tuple[str, bool]:
	'''Render one markdown line; returns (rendered_line, in_code_for_the_next_line).'''
	if line.lstrip().startswith("```"):
		if not in_code:
			lang = line.lstrip()[3:].strip()
			return f"\x1b[2m┄┄{(' ' + lang) if lang else ''}\x1b[22m", True
		return "\x1b[2m┄┄\x1b[22m", False
	if in_code:
		return f"\x1b[2m{line}\x1b[22m", True

	m = re.match(r"^(\s{0,3})(#{1,6})\s+(.*)$", line)
	if m:
		open_codes = "\x1b[1m" + ("\x1b[4m" if len(m.group(2)) == 1 else "")
		return f"{m.group(1)}{open_codes}{_inline(m.group(3))}\x1b[24m\x1b[22m", False

	if re.match(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$", line):
		return "\x1b[2m" + "─" * 30 + "\x1b[22m", False

	m = re.match(r"^(\s{0,3})>\s?(.*)$", line)
	if m:
		return f"\x1b[34m▎\x1b[39m {_inline(m.group(2))}", False

	m = re.match(r"^(\s*)([-*+]|\d{1,3}\.)\s+(.*)$", line)
	if m:
		return f"{m.group(1)}\x1b[36m{m.group(2)}\x1b[39m {_inline(m.group(3))}", False

	return _inline(line), False


def markdown_to_ansi(text: str) -> str:
	'''Pure function: markdown subset → ANSI codes. No tty/config checks — see render_markdown().'''
	out = []
	in_code = False
	for line in str(text).split("\n"):
		rendered, in_code = _render_line(line, in_code)
		out.append(rendered)
	return "\n".join(out)


class MarkdownStream:
	'''Incremental line renderer for streamed output.

	feed(chunk) returns the rendered text of every complete line seen so far
	("" when the chunk only extends the current partial line); flush() renders
	the trailing partial line at the end of the stream. Fence state is kept
	across calls, so a code block spanning chunks still renders correctly.'''

	def __init__(self):
		self._buf = ""
		self._in_code = False

	def feed(self, chunk: str) -> str:
		self._buf += str(chunk)
		out = []
		while "\n" in self._buf:
			line, self._buf = self._buf.split("\n", 1)
			rendered, self._in_code = _render_line(line, self._in_code)
			out.append(rendered)
		return ("\n".join(out) + "\n") if out else ""

	def flush(self) -> str:
		if not self._buf:
			return ""
		rendered, self._in_code = _render_line(self._buf, self._in_code)
		self._buf = ""
		return rendered


def rendering_enabled() -> bool:
	'''Interactive rendering is on: stdout is a tty, config.RENDER_MARKDOWN is on,
	NO_COLOR is unset.'''
	return RENDER_MARKDOWN and not os.environ.get("NO_COLOR") and sys.stdout.isatty()


def render_markdown(text: str) -> str:
	'''Render a whole markdown text for the terminal — the non-streaming variant of
	MarkdownStream; a no-op when rendering is disabled.'''
	if not rendering_enabled():
		return str(text)
	return markdown_to_ansi(text)