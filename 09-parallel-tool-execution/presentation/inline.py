"""Inline Markdown rendering for emphasis, code, links, and math spans."""

import re
from .math import latex_to_text

def _looks_like_inline_math(body: str) -> bool:
	'''Decide whether a $...$ span really is maths.

	Currency is the false positive that matters — "$100 and $200" must not be
	eaten — so a span with whitespace is only accepted when it carries an
	unambiguous LaTeX signal. A short space-free span is accepted either way,
	which is what lets $x$ and $x^2$ through.
	'''
	if not body or body != body.strip():
		return False
	if any(c in body for c in "\\^_{}"):
		return True
	if any(c.isspace() for c in body):
		return False
	return len(body) <= 3


def _emphasis(text: str) -> str:
	'''Bold / italic / strikethrough — split out so it can also run on a link label.

	ASCII-only word boundaries: CJK text has no spaces around emphasis, so \\w
	lookarounds would wrongly block emphasis adjacent to CJK text (and would italicize expressions such as 2*3*4).
	'''
	text = re.sub(r"\*\*\*(.+?)\*\*\*", lambda m: f"\x1b[1;3m{m.group(1)}\x1b[22m\x1b[23m", text)
	text = re.sub(r"\*\*(.+?)\*\*", lambda m: f"\x1b[1m{m.group(1)}\x1b[22m", text)
	text = re.sub(r"(?<![A-Za-z0-9*])\*([^*\n]+?)\*(?![A-Za-z0-9*])", lambda m: f"\x1b[3m{m.group(1)}\x1b[23m", text)
	text = re.sub(r"(?<![A-Za-z0-9_])_([^_\n]+?)_(?![A-Za-z0-9_])", lambda m: f"\x1b[3m{m.group(1)}\x1b[23m", text)
	text = re.sub(r"~~(.+?)~~", lambda m: f"\x1b[9m{m.group(1)}\x1b[29m", text)
	return text


def _inline(text: str) -> str:
	'''Render inline markdown: code spans, math, links, emphasis.

	Everything that must not be re-read by a later rule is stashed behind a
	placeholder first. Links need that treatment for a reason that is easy to
	miss: the link rule looks for `[label](url)`, and *every* escape sequence
	this module emits contains a `[`. Run the link rule after any styling has
	been inserted and it matches across escape sequences — `**b** ... [l](u)`
	used to come out mangled because the `[` of `\\x1b[1m` was read as a link
	opening. Stashing keeps the link rule looking at source text only.
	'''
	# Code spans first so * and _ inside them are not treated as emphasis.
	codes = []
	def _stash(m):
		"""Perform the  stash operation. Args: m: Value used by this operation. Returns: The result of this operation."""
		codes.append(m.group(1))
		return f"\x00{len(codes) - 1}\x00"
	text = re.sub(r"`([^`]+)`", _stash, text)

	# Math next, for the same reason and one more: $x_1 + y_2$ contains two
	# underscores, which the italic rule would happily pair up.
	maths = []
	def _stash_math(m):
		"""Perform the  stash math operation. Args: m: Value used by this operation. Returns: The result of this operation."""
		body = m.group(1) if m.group(1) is not None else m.group(2)
		if not _looks_like_inline_math(body):
			return m.group(0)
		maths.append(body)
		return f"\x01{len(maths) - 1}\x01"
	text = re.sub(r"\$([^$\n]+?)\$|\\\((.+?)\\\)", _stash_math, text)

	# Links last of the three, styling the label on its own — safe, because at
	# this point the label is still plain source text.
	links = []
	def _stash_link(m):
		"""Perform the  stash link operation. Args: m: Value used by this operation. Returns: The result of this operation."""
		links.append((_emphasis(m.group(1)), m.group(2)))
		return f"\x02{len(links) - 1}\x02"
	text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _stash_link, text)

	text = _emphasis(text)

	text = re.sub(r"\x00(\d+)\x00", lambda m: f"\x1b[36m{codes[int(m.group(1))]}\x1b[39m", text)
	text = re.sub(r"\x01(\d+)\x01", lambda m: _math_span(maths[int(m.group(1))]), text)
	text = re.sub(r"\x02(\d+)\x02", lambda m: (
		f"\x1b[4m{links[int(m.group(1))][0]}\x1b[24m"
		f"\x1b[2m ({links[int(m.group(1))][1]})\x1b[22m"), text)
	return text


def _math_span(body: str) -> str:
	'''Math is magenta: code spans are already cyan, and the two should not be
	confused for each other.'''
	return f"\x1b[35m{latex_to_text(body)}\x1b[39m"
