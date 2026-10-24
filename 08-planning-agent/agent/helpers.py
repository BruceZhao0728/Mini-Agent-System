"""Pure helpers for streamed replies and planner response validation."""

import json
from config import PLAN_MAX_STEPS



def _message_dict(msg) -> dict:
	'''Serialize an assembled streamed message the way the SDK's model_dump() would have —
	role/content/reasoning_content/tool_calls. Used by the response log gates, which have
	no SDK object to dump now that the call is streamed.'''
	d = {"role": "assistant", "content": msg.content}
	reasoning = getattr(msg, "reasoning_content", None)
	if reasoning is not None:
		d["reasoning_content"] = reasoning
	if msg.tool_calls:
		d["tool_calls"] = [{
			"id": tc.id,
			"type": "function",
			"function": {"name": tc.function.name, "arguments": tc.function.arguments},
		} for tc in msg.tool_calls]
	# Not part of the message the API takes back — finish_reason belongs to the
	# choice, not the message, and is never echoed into the history. It is here
	# because the log is the only run record: without it a generation the server
	# cut short is indistinguishable from one that ended on its own.
	finish_reason = getattr(msg, "finish_reason", None)
	if finish_reason is not None:
		d["finish_reason"] = finish_reason
	return d


def _answer_warning(content: str, finish_reason) -> str:
	'''Judge a *closing* reply (one with no tool calls) that must not be reported as
	a successful answer. Returns a warning line, or "" when the reply is fine.

	Callers must only pass closing replies: a tool-calling round legitimately ends
	with a bare "Thought: …" and no "Final Answer:", which is not a defect.

	Three shapes, all of which used to pass silently:

	- the answer channel produced nothing — the entire completion went to
	  reasoning_content and the user is shown only the model's deliberation;
	- the server stopped at an output cap (finish_reason="length"), so a partial
	  answer was returned as if it were whole;
	- the reply opened the ReAct format ("Thought:") and never reached
	  "Final Answer:" — the signature of a generation that stopped mid-answer.
	  Measured over the 111 closing replies on record in 05/06/07: that
	  combination occurs exactly once, and it is the 2026-10-08 incident
	  (reasoning 4,791 chars, then 72 chars of answer, cut mid-word).

	A reply carrying neither marker is normal — the model answers plain chat
	without the format — and that is the `[Done]` path by design.
	'''
	if finish_reason == "length":
		return ("⚠ reply cut off — the server stopped at an output limit "
		        "(finish_reason=length); what you see is a partial answer")
	if not content.strip():
		return ("⚠ empty reply — the whole completion went to the thinking channel, "
		        "so nothing was answered; the Finish: line in the log says why it ended")
	if "Thought:" in content and "Final Answer:" not in content:
		return ("⚠ incomplete reply — the ReAct format was opened with 'Thought:' and "
		        "never reached 'Final Answer:'; the generation stopped mid-answer")
	return ""

def _parse_plan_response(content: str, max_steps: int = PLAN_MAX_STEPS) -> tuple:
	"""Return (needs_plan, steps, error). Invalid output returns an error."""
	try:
		data = json.loads((content or "").strip())
	except json.JSONDecodeError as e:
		return False, [], f"invalid JSON: {e}"

	if not isinstance(data, dict):
		return False, [], "top-level JSON value must be an object"
	if not isinstance(data.get("needs_plan"), bool):
		return False, [], "needs_plan must be a boolean"

	needs_plan = data["needs_plan"]
	steps = data.get("steps")
	if not isinstance(steps, list):
		return False, [], "steps must be a list"
	if any(not isinstance(step, str) or not step.strip() for step in steps):
		return False, [], "every step must be a non-empty string"
	steps = [step.strip() for step in steps]

	if not needs_plan:
		return False, [], ""
	if len(steps) < 2:
		return False, [], "a planned task must contain at least two steps"
	if len(steps) > max_steps:
		return False, [], f"plan has {len(steps)} steps; limit is {max_steps}"

	return True, steps, ""

__all__ = [name for name in globals() if not name.startswith("__")] + ["_message_dict", "_answer_warning", "_parse_plan_response"]
