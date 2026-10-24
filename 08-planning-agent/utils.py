"""Text helpers used by tools and agent execution."""

from config import MAX_OBS_CHARS


def clip(text: str, head: int = 6000, tail: int = 1500) -> str:
	"""Clip long tool output while preserving its beginning and end.

	Args:
		text: Arbitrary content converted to text before measuring.
		head: Maximum prefix characters retained when clipping occurs.
		tail: Maximum suffix characters retained when clipping occurs.

	Returns:
		The original text when within MAX_OBS_CHARS, otherwise a clipped string.
	"""
	s = str(text)
	if len(s) <= MAX_OBS_CHARS:
		return s
	return f"{s[:head]}\n…[Clipped {len(s) - head - tail} characters]…\n{s[-tail:]}"
