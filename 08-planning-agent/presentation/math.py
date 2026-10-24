"""Best-effort conversion from common LaTeX fragments to terminal Unicode."""

import re

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
		"""Perform the script operation. Args: match: Value used by this operation; table: Value used by this operation; marker: Value used by this operation. Returns: The result of this operation."""
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


