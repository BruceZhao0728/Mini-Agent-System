"""Planner request, plan validation, and plan status rendering."""

from config import (
    MODEL, PLAN_PROMPT, PLAN_MAX_TOKENS, THINKING, REQUEST_USAGE,
    MEMORY_INDEX_HEADER,
)
from .helpers import _parse_plan_response

class PlanningMixin:
	"""Group related behavior for planning."""
	def _request_plan(self, prompt: str) -> tuple:
		"""Classify a request and, when needed, return its ordered steps."""
		try:
			print("🧭 Planning request…", flush=True)
			request = {
				"model": MODEL,
				"messages": [
					{"role": "system", "content": PLAN_PROMPT},
					{"role": "user", "content": prompt},
				],
				"max_tokens": PLAN_MAX_TOKENS,
				"stream": True,
				"extra_body": {"thinking": {"type": THINKING}},
			}
			if REQUEST_USAGE:
				request["stream_options"] = {"include_usage": True}
			stream = self.client.chat.completions.create(**request)
			msg = self._consume_stream(stream, show_content=False)
			if msg.usage is not None:
				self.last_usage = msg.usage
			needs_plan, steps, error = _parse_plan_response(msg.content)
		except Exception as e:
			return False, [], f"Planner request failed: {type(e).__name__}: {e}"
		if error:
			return False, [], f"Planner response rejected: {error}"
		return needs_plan, steps, ""

	def _render_plan(self) -> str:
		"""Render the current plan into a compact, model-visible status block."""
		if not self.plan:
			return ""
		lines = ["Current task plan:"]
		for i, step in enumerate(self.plan, 1):
			marker = "→" if step["status"] == "in_progress" else (
				"✓" if step["status"] == "completed" else (
				"✗" if step["status"] == "failed" else "·"))
			lines.append(f"{marker} {i}. [{step['status']}] {step['text']}")
		return "\n".join(lines)

	def _refresh_plan_context(self):
		"""Refresh the system message; it is protected by compaction's keep_head."""
		if not self.messages or self.messages[0].get("role") != "system":
			return
		content = self.system_prompt
		if self.memory_index:
			content += MEMORY_INDEX_HEADER + self.memory_index
		plan_text = self._render_plan()
		if plan_text:
			content += "\n\n" + plan_text + (
				"\nExecute the current steps in order, proceeding after each step is completed. "
				"After the last step, answer the original user request with Final Answer.")
		self.messages[0]["content"] = content

	@staticmethod
	def _execution_failed(result: str) -> bool:
		"""Detect terminal failures returned by the existing ReAct loop."""
		return (result == "Max steps reached"
				or result.startswith("Error during LLM call ("))
