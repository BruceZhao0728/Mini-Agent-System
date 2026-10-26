"""Planner request, plan validation, and plan status rendering."""

import time
from config import (
    MODEL, PLAN_PROMPT, PLAN_MAX_TOKENS, THINKING, REQUEST_USAGE,
    MEMORY_INDEX_HEADER,
)
from tracing import hash_text
from .helpers import _parse_plan_response

class PlanningMixin:
	"""Group related behavior for planning."""
	def _request_plan(self, prompt: str) -> tuple:
		"""Classify a request and, when needed, return its ordered steps."""
		started = time.monotonic()
		span = self.tracer.new_span(self._turn_span)
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
			usage = getattr(msg, "usage", None)
			self.tracer.emit(
				"model_request", actor="planner", span_id=span,
				parent_span_id=self._turn_span, component="planner",
				model=MODEL, stream=True, message_count=2, tools_count=0,
				request_chars=len(prompt), prompt_hash=hash_text(prompt),
				duration_ms=round((time.monotonic() - started) * 1000, 1),
				input_tokens=getattr(usage, "prompt_tokens", None),
				output_tokens=getattr(usage, "completion_tokens", None),
				total_tokens=getattr(usage, "total_tokens", None),
				finish_reason=getattr(msg, "finish_reason", None),
				status="ok")
		except Exception as e:
			self.tracer.emit(
				"error", actor="planner", span_id=span,
				parent_span_id=self._turn_span, component="planner",
				error_type=type(e).__name__, error=str(e)[:500],
				retryable=False)
			return False, [], f"Planner request failed: {type(e).__name__}: {e}"
		if error:
			self.tracer.emit(
				"verification", actor="planner", span_id=span,
				parent_span_id=self._turn_span, check="plan_schema",
				status="fail", layer="structure", reason=error[:500])
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
