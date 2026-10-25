"""Markdown table parsing, width calculation, wrapping, and terminal layout."""

import re
import shutil
import unicodedata
from .terminal import _ANSI_RE, _RESET, display_width
from .inline import _inline

TABLE_MAX_COL_WIDTH = 60
TABLE_MIN_COL_WIDTH = 8

def _split_row(line: str) -> list:
	'''Cells of a markdown table row; outer pipes stripped, cells trimmed.

	A `|` inside a code span or a math span does **not** separate cells, and a
	`\\|` is a literal pipe. This matters more than it looks: GFM asks for the
	escape, models routinely write `` `b | a` `` or `$p^k | n$` without it, and
	splitting on those turns one cell into three. Measured symptom — a 4-column
	table of number-theory concepts came out with 8 columns, the extra four
	empty, and sentences chopped mid-clause at every stray pipe.
	'''
	s = line.strip()
	if s.startswith("|"):
		s = s[1:]
	if s.endswith("|") and not s.endswith("\\|"):
		s = s[:-1]

	cells, current = [], []
	in_code = in_math = False
	i = 0
	while i < len(s):
		ch = s[i]
		if ch == "\\" and s[i + 1:i + 2] == "|":
			current.append("|")		# \| is an escaped, literal pipe
			i += 2
			continue
		if ch == "`":
			in_code = not in_code
		elif ch == "$" and not in_code:
			in_math = not in_math
		elif ch == "|" and not in_code and not in_math:
			cells.append("".join(current))
			current = []
			i += 1
			continue
		current.append(ch)
		i += 1
	cells.append("".join(current))
	return [c.strip() for c in cells]


def _is_separator_row(line: str) -> bool:
	'''The |---|---| row. Its presence is what proves a table is a table.'''
	cells = _split_row(line)
	return bool(cells) and all(re.fullmatch(r":?-{1,}:?", c) for c in cells)


def _is_table_row(line: str) -> bool:
	'''Conservative on purpose: a prose line with one stray pipe should not be
	held back as a possible table header.'''
	s = line.strip()
	if not s or "|" not in s:
		return False
	return s.startswith("|") or s.endswith("|") or s.count("|") >= 2


def _wrap_display(text: str, width: int) -> list:
	'''Split rendered text into lines of at most `width` display columns.

	ANSI-aware (an escape sequence costs nothing and is never split) and
	CJK-aware (a double-width glyph never straddles the boundary). Wrapping
	prefers the last space on the line, the way a word processor would.

	Wrapping rather than truncating is deliberate: a table is a display of
	content the user asked for, and an `…` in the middle of a definition
	answers a different question than the one that was asked. A too-wide table
	gets taller, not less complete.
	'''
	if width <= 0 or display_width(text) <= width:
		return [text]

	lines, current, used, space_at = [], [], 0, None
	i = 0
	while i < len(text):
		m = _ANSI_RE.match(text, i)
		if m:
			current.append(m.group(0))
			i = m.end()
			continue
		ch = text[i]
		w = 0 if unicodedata.combining(ch) else (
			2 if unicodedata.east_asian_width(ch) in "WF" else 1)
		if used + w > width and current:
			if space_at is not None:
				lines.append("".join(current[:space_at]))
				current = current[space_at + 1:]	# the break consumes the space
				used = display_width("".join(current))
				space_at = None
			else:
				lines.append("".join(current))
				current, used = [], 0
			continue			# retry this character on the fresh line
		current.append(ch)
		used += w
		if ch == " ":
			space_at = len(current) - 1
		i += 1
	lines.append("".join(current))

	if "\x1b" in text:
		# A style that straddles a break would otherwise bleed into the next
		# cell (or the border). Resetting per line loses bold on continuation
		# lines, which is a smaller lie than colouring characters that are not
		# styled.
		lines = [line + _RESET if line else line for line in lines]
	return lines


def _fit_widths(widths: list, budget: int) -> list:
	'''Shrink the widest columns until the table fits `budget` columns.

	Columns get narrower and `_wrap_display` makes the rows taller; nothing is
	dropped. The overhead is the │ borders plus the one space either side of
	every cell.
	'''
	widths = list(widths)
	overhead = len(widths) + 1 + 2 * len(widths)
	while sum(widths) + overhead > budget:
		widest = max(range(len(widths)), key=lambda i: widths[i])
		if widths[widest] <= TABLE_MIN_COL_WIDTH:
			break
		widths[widest] -= 1
	return widths


def _table_budget() -> int:
	'''Terminal columns a table may occupy. shutil falls back to $COLUMNS and
	then to 80, so a piped run gets a sane number rather than an exception.'''
	return max(shutil.get_terminal_size((80, 24)).columns, TABLE_MIN_COL_WIDTH * 2)


def _render_table(rows: list, budget: int = None) -> str:
	'''rows[0] is the header, rows[1] the separator (dropped), the rest is data.

	Cells are inline-rendered first and measured afterwards, because _inline can
	change a cell's width — [text](url) becomes "text (url)". Padding computed
	on the raw source would be wrong for exactly those cells.
	'''
	header = [_inline(c) for c in _split_row(rows[0])]
	data = [[_inline(c) for c in _split_row(r)] for r in rows[2:]]
	ncols = max([len(header)] + [len(r) for r in data])
	header += [""] * (ncols - len(header))
	data = [r + [""] * (ncols - len(r)) for r in data]

	widths = []
	for j in range(ncols):
		w = display_width(header[j])
		for r in data:
			w = max(w, display_width(r[j]))
		widths.append(min(w, TABLE_MAX_COL_WIDTH))
	widths = _fit_widths(widths, _table_budget() if budget is None else budget)

	dim = lambda s: f"\x1b[2m{s}\x1b[22m"

	def row_lines(cells, bold=False):
		'''One entry per *visual* line: a cell taller than one line makes the
		whole row taller, and every other cell in that row is padded to match.'''
		wrapped = [_wrap_display(cell, widths[j]) for j, cell in enumerate(cells)]
		out = []
		for i in range(max(len(w) for w in wrapped)):
			parts = []
			for j, cell_lines in enumerate(wrapped):
				seg = cell_lines[i] if i < len(cell_lines) else ""
				if bold and seg:
					seg = f"\x1b[1m{seg}\x1b[22m"
				parts.append(" " + seg + " " * (widths[j] - display_width(seg)) + " ")
			out.append(dim("│") + dim("│").join(parts) + dim("│"))
		return out

	rule = "├" + "┼".join("─" * (w + 2) for w in widths) + "┤"
	lines = row_lines(header, bold=True) + [dim(rule)]
	for r in data:
		lines += row_lines(r)
	return "\n".join(lines)
