import os
import re
import shutil
import sys
import unicodedata

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
#
# Three things in here are block-aware or width-aware, and both matter:
#   * a table is a *block*, not a line — you need the separator row to know it
#     is a table at all, and every row to know how wide each column is — so the
#     renderer holds rows back until the block ends;
#   * CJK glyphs occupy two terminal columns, so all column arithmetic uses
#     display_width(), never len(). A table aligned by len() looks ragged;
#   * LaTeX is converted to Unicode where a faithful conversion exists
#     (greeks, operators, ^/_ scripts, \frac, \sqrt) and otherwise left as
#     readable source. A terminal cannot typeset maths; pretending otherwise
#     would be worse than showing the source.
# ---------------------------------------------------------------------------

TABLE_MAX_COL_WIDTH = 60	# no single column takes more than this before the terminal budget is applied
TABLE_MIN_COL_WIDTH = 8		# ...and none is squeezed below this before wrapping takes over

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


# --- LaTeX → Unicode -------------------------------------------------------

_LATEX_SYMBOLS = {
	# Greek
	r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ",
	r"\epsilon": "ε", r"\varepsilon": "ε", r"\zeta": "ζ", r"\eta": "η",
	r"\theta": "θ", r"\vartheta": "ϑ", r"\iota": "ι", r"\kappa": "κ",
	r"\lambda": "λ", r"\mu": "μ", r"\nu": "ν", r"\xi": "ξ", r"\pi": "π",
	r"\rho": "ρ", r"\sigma": "σ", r"\tau": "τ", r"\upsilon": "υ",
	r"\phi": "φ", r"\varphi": "φ", r"\chi": "χ", r"\psi": "ψ", r"\omega": "ω",
	r"\Gamma": "Γ", r"\Delta": "Δ", r"\Theta": "Θ", r"\Lambda": "Λ",
	r"\Xi": "Ξ", r"\Pi": "Π", r"\Sigma": "Σ", r"\Upsilon": "Υ",
	r"\Phi": "Φ", r"\Psi": "Ψ", r"\Omega": "Ω",
	# relations
	r"\leq": "≤", r"\le": "≤", r"\geq": "≥", r"\ge": "≥",
	r"\neq": "≠", r"\ne": "≠", r"\approx": "≈", r"\equiv": "≡",
	r"\sim": "∼", r"\simeq": "≃", r"\propto": "∝", r"\ll": "≪", r"\gg": "≫",
	r"\in": "∈", r"\notin": "∉", r"\ni": "∋", r"\subset": "⊂",
	r"\subseteq": "⊆", r"\supset": "⊃", r"\supseteq": "⊇",
	r"\perp": "⊥", r"\parallel": "∥", r"\cong": "≅",
	r"\mid": "∣", r"\nmid": "∤", r"\vert": "|", r"\lvert": "|", r"\rvert": "|",
	r"\Vert": "‖", r"\lVert": "‖", r"\rVert": "‖", r"\colon": ":",
	r"\lfloor": "⌊", r"\rfloor": "⌋", r"\lceil": "⌈", r"\rceil": "⌉",
	r"\langle": "⟨", r"\rangle": "⟩",
	r"\aleph": "ℵ", r"\hbar": "ℏ", r"\ell": "ℓ", r"\wp": "℘",
	r"\Re": "ℜ", r"\Im": "ℑ", r"\square": "□", r"\blacksquare": "■",
	# Function names: dropping the backslash is the whole conversion.
	r"\gcd": "gcd", r"\lcm": "lcm", r"\log": "log", r"\ln": "ln",
	r"\exp": "exp", r"\sinh": "sinh", r"\cosh": "cosh", r"\tanh": "tanh",
	r"\sin": "sin", r"\cos": "cos", r"\tan": "tan", r"\cot": "cot",
	r"\sec": "sec", r"\csc": "csc", r"\max": "max", r"\min": "min",
	r"\lim": "lim", r"\sup": "sup", r"\inf": "inf", r"\det": "det",
	r"\dim": "dim", r"\ker": "ker", r"\deg": "deg", r"\arg": "arg",
	r"\bmod": " mod ", r"\displaystyle": "", r"\textstyle": "",
	# operators
	r"\times": "×", r"\cdot": "·", r"\div": "÷", r"\pm": "±", r"\mp": "∓",
	r"\ast": "∗", r"\star": "⋆", r"\circ": "∘", r"\bullet": "•",
	r"\oplus": "⊕", r"\otimes": "⊗", r"\odot": "⊙",
	r"\sum": "∑", r"\prod": "∏", r"\coprod": "∐",
	r"\int": "∫", r"\iint": "∬", r"\iiint": "∭", r"\oint": "∮",
	r"\impliedby": "⟸", r"\Longleftrightarrow": "⟺", r"\Longrightarrow": "⟹",
	r"\Longleftarrow": "⟸", r"\iff": "⟺", r"\implies": "⟹",
	r"\forall": "∀", r"\exists": "∃", r"\nexists": "∄",
	r"\neg": "¬", r"\lnot": "¬", r"\land": "∧", r"\wedge": "∧",
	r"\lor": "∨", r"\vee": "∨", r"\cap": "∩", r"\cup": "∪",
	r"\setminus": "∖", r"\therefore": "∴", r"\because": "∵",
	# arrows
	r"\longrightarrow": "⟶", r"\longleftarrow": "⟵",
	r"\rightarrow": "→", r"\leftarrow": "←", r"\to": "→",
	r"\Rightarrow": "⇒", r"\Leftarrow": "⇐",
	r"\leftrightarrow": "↔", r"\Leftrightarrow": "⇔",
	r"\mapsto": "↦", r"\uparrow": "↑", r"\downarrow": "↓",
	# misc
	r"\infty": "∞", r"\partial": "∂", r"\nabla": "∇",
	r"\emptyset": "∅", r"\varnothing": "∅",
	r"\angle": "∠", r"\degree": "°", r"\deg": "°",
	r"\ldots": "…", r"\dots": "…", r"\cdots": "⋯", r"\vdots": "⋮", r"\ddots": "⋱",
	r"\qquad": "  ", r"\quad": " ",
	r"\%": "%", r"\$": "$", r"\&": "&", r"\#": "#",
}

# Longest first so \leq is never matched as \le, \varnothing never as \varnothing.
_LATEX_SYMBOL_RE = re.compile("|".join(
	re.escape(k) for k in sorted(_LATEX_SYMBOLS, key=len, reverse=True)))

_BLACKBOARD = {
	"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "P": "ℙ", "E": "𝔼",
}

_SUPERSCRIPT = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵",
				"6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻",
				"=": "⁼", "(": "⁽", ")": "⁾", "n": "ⁿ", "i": "ⁱ"}

_SUBSCRIPT = {"0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅",
			  "6": "₆", "7": "₇", "8": "₈", "9": "₉", "+": "₊", "-": "₋",
			  "=": "₌", "(": "₍", ")": "₎", "a": "ₐ", "e": "ₑ", "i": "ᵢ",
			  "o": "ₒ", "x": "ₓ", "n": "ₙ", "k": "ₖ", "m": "ₘ", "p": "ₚ",
			  "s": "ₛ", "t": "ₜ", "j": "ⱼ"}


def _convert_script(body: str, table: dict):
	'''Whole-or-nothing: a partially converted script is worse than none.'''
	out = []
	for ch in body:
		if ch not in table:
			return None
		out.append(table[ch])
	return "".join(out)


def latex_to_text(tex: str) -> str:
	'''Convert a LaTeX fragment to the closest Unicode a terminal can show.

	A best-effort display transform, not a parser. Anything it does not
	recognise is left as readable source (braces dropped, spacing collapsed)
	rather than mangled — a half-rendered formula is worse than source.
	'''
	s = str(tex)
	# One level of nested braces is understood; \frac{\sqrt{b^2-4ac}}{2a} is the
	# shape that forces it, and it is a common one.
	arg = r"((?:[^{}]|\{[^{}]*\})*)"

	s = re.sub(r"\\(?:left|right|bigl|bigr|Bigl|Bigr|biggl|biggr|Biggl|Biggr"
			   r"|big|Big|bigg|Bigg)\s*", "", s)
	# \{ and \} are literal characters, not grouping — hide them from the brace
	# strip at the end. (\left\{ x \right\} used to come out as "\ x \".)
	s = s.replace(r"\{", "\x03").replace(r"\}", "\x04")
	s = re.sub(r"\\(?:text|mathrm|mathbf|mathit|mathsf|operatorname)\{" + arg + r"\}", r"\1", s)
	s = re.sub(r"\\mathbb\{([A-Z])\}", lambda m: _BLACKBOARD.get(m.group(1), m.group(0)), s)
	# Roots before \frac: it turns \sqrt{b^2-4ac} into √(b^2-4ac) — parentheses,
	# no braces — which is what lets \frac's argument pattern stay shallow.
	s = re.sub(r"\\sqrt\[([^\]]*)\]\{" + arg + r"\}", r"∛(\2)", s)
	s = re.sub(r"\\sqrt\{" + arg + r"\}", r"√(\1)", s)
	s = re.sub(r"\\[dtc]?frac\{" + arg + r"\}\{" + arg + r"\}", r"(\1)/(\2)", s)
	s = re.sub(r"\\binom\{" + arg + r"\}\{" + arg + r"\}", r"C(\1,\2)", s)
	s = re.sub(r"\\pmod\{" + arg + r"\}", r" (mod \1)", s)
	# Accents become combining marks — the closest a terminal gets to \hat{x}.
	# Without this the brace strip turns \hat{y} into "\haty", which is not even
	# readable source any more.
	for command, mark in ((r"\widehat", "\u0302"), (r"\hat", "\u0302"),
						  (r"\overline", "\u0304"), (r"\bar", "\u0304"),
						  (r"\widetilde", "\u0303"), (r"\tilde", "\u0303"),
						  (r"\vec", "\u20d7"), (r"\ddot", "\u0308"),
						  (r"\dot", "\u0307"), (r"\underline", "\u0332")):
		s = re.sub(re.escape(command) + r"\{([^{}]*)\}",
				   lambda m, k=mark: m.group(1) + k, s)

	def script(match, table, marker):
		braced = match.group(1) is not None
		body = match.group(1) if braced else match.group(2)
		converted = _convert_script(body, table)
		if converted is not None:
			return converted
		# Unicode has no ᵖ or ᵢ for everything. Fall back to a parenthesised
		# form for the braced case — e^{i\pi} reads far better as e^(iπ) than
		# as e^iπ, which is what stripping the braces would leave.
		return f"{marker}({body})" if braced else f"{marker}{body}"

	s = re.sub(r"\^\{([^{}]*)\}|\^(\S)", lambda m: script(m, _SUPERSCRIPT, "^"), s)
	s = re.sub(r"_\{([^{}]*)\}|_(\S)", lambda m: script(m, _SUBSCRIPT, "_"), s)
	s = re.sub(r"[{}]", "", s)
	s = s.replace("\x03", "{").replace("\x04", "}")
	s = _LATEX_SYMBOL_RE.sub(lambda m: _LATEX_SYMBOLS[m.group(0)], s)
	s = re.sub(r"\\[,;: ]", " ", s)		# \, \; \: and \  are all thin spaces
	s = s.replace(r"\!", "")			# ...and \! is a negative one
	s = re.sub(r"[ \t]{2,}", " ", s)
	return s.strip()


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
	lookarounds would wrongly block 他说*很好*啊 (and would italicise 2*3*4).
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
		codes.append(m.group(1))
		return f"\x00{len(codes) - 1}\x00"
	text = re.sub(r"`([^`]+)`", _stash, text)

	# Math next, for the same reason and one more: $x_1 + y_2$ contains two
	# underscores, which the italic rule would happily pair up.
	maths = []
	def _stash_math(m):
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


# --- tables ----------------------------------------------------------------

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


# --- line ----------------------------------------------------------------

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
		self._buf = ""
		self._in_code = False
		self._in_math = False
		self._pending = None	# a line held back in case it is a table header
		self._table = None		# confirmed table rows, held until the block ends
		self._ncols = 0

	def _emit(self, line: str) -> str:
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
		self._buf += str(chunk)
		out = []
		while "\n" in self._buf:
			line, self._buf = self._buf.split("\n", 1)
			out.extend(self._consume(line))
		return ("\n".join(out) + "\n") if out else ""

	def flush(self) -> str:
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
