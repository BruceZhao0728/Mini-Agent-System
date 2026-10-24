"""Fixed-bottom rendering for an active task plan."""

import shutil
import sys

from .terminal import display_width


_MARKERS = {
	"pending": "○",
	"in_progress": "●",
	"completed": "✓",
	"failed": "✗",
}


def _fit_line(text: str, width: int) -> str:
	"""Clip a line to terminal display columns, leaving room for an ellipsis."""
	if width <= 0:
		return ""
	if display_width(text) <= width:
		return text
	if width == 1:
		return "…"
	result = ""
	for char in text:
		if display_width(result + char + "…") > width:
			break
		result += char
	return result + "…"


def render_plan_progress(plan: list, width: int) -> list:
	"""Return one header and one compact status line per plan step."""
	completed = sum(step.get("status") == "completed" for step in plan)
	lines = [f"Plan · {completed}/{len(plan)} completed"]
	for index, step in enumerate(plan, 1):
		status = step.get("status", "pending")
		marker = _MARKERS.get(status, "○")
		lines.append(_fit_line(f"{marker} {index}. {step.get('text', '')}", width))
	return [_fit_line(line, width) for line in lines]


class PlanProgressDisplay:
	"""Reserve terminal rows for a live plan and restore the scroll region on close."""
	def __init__(self, stream=None):
		self.stream = stream or sys.stdout
		self.active = False
		self.lines = []
		self.output_bottom = 1

	def update(self, plan: list):
		"""Draw or redraw the plan at the bottom of a TTY."""
		if not getattr(self.stream, "isatty", lambda: False)() or not plan:
			return
		size = shutil.get_terminal_size((80, 24))
		# Leave the final column unused so terminals with delayed autowrap do not
		# wrap a footer line into the following row.
		width = max(1, size.columns - 1)
		self.lines = render_plan_progress(plan, width)
		rows = max(2, size.lines)
		footer_height = min(len(self.lines), rows - 1)
		output_bottom = rows - footer_height
		self.output_bottom = output_bottom
		# DECSTBM confines normal streaming output to the area above the plan.
		self.stream.write(f"\x1b[1;{output_bottom}r")
		self.active = True
		self._draw(rows, footer_height)

	def _draw(self, rows: int, footer_height: int):
		start = rows - footer_height + 1
		for offset in range(footer_height):
			line = self.lines[offset] if offset < len(self.lines) else ""
			self.stream.write(f"\x1b[{start + offset};1H\x1b[2K{line}")
		# Keep subsequent print/stream writes at the bottom of the scrolling area.
		self.stream.write(f"\x1b[{rows - footer_height};1H")
		self.stream.flush()

	def close(self):
		"""Restore normal terminal scrolling while leaving the last plan visible."""
		if not self.active:
			return
		self.stream.write(f"\x1b[r\x1b[{self.output_bottom};1H\n")
		self.stream.write("\n".join(self.lines) + "\n")
		self.stream.flush()
		self.active = False


__all__ = ["PlanProgressDisplay", "render_plan_progress"]
