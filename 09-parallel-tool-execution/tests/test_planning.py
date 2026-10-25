"""Planner response, routing, status, and fallback assertions."""

import contextlib, io
from types import SimpleNamespace
from agent import ReActAgent
from agent.helpers import _parse_plan_response
from config import MAX_ROUNDS, THINKING, REQUEST_USAGE

def fake_chunk(content=None, reasoning=None, finish=None, usage=None, with_choices=True):
    """Build the minimal streamed response chunk used by agent assertions."""
    delta = SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=None)
    choices = [SimpleNamespace(delta=delta, finish_reason=finish)] if with_choices else []
    return SimpleNamespace(choices=choices, usage=usage)

usage_obj = SimpleNamespace(prompt_tokens=10, completion_tokens=3, total_tokens=13)


# Test invariant or expected behavior.
assert _parse_plan_response('{"needs_plan": false, "steps": []}') == (False, [], "")
assert _parse_plan_response(
    '{"needs_plan": true, "steps": ["read", "summarize"]}') == (
        True, ["read", "summarize"], "")
assert _parse_plan_response("not json")[2].startswith("invalid JSON")
assert _parse_plan_response('{"needs_plan": "yes", "steps": []}')[2]
assert _parse_plan_response(
    '{"needs_plan": true, "steps": ["only one"]}')[2]
assert _parse_plan_response(
    '{"needs_plan": true, "steps": ["a", "b", "c"]}', max_steps=2)[2]
print("Planning parser: valid and invalid JSON cases")

# Test invariant or expected behavior.
agent = ReActAgent()
agent._request_plan = lambda prompt: (True, ["inspect files", "summarize findings"], "")
executed = []
def fake_react(prompt, first_turn=False):
    """Perform the fake react operation. Args: prompt: Value used by this operation; first_turn: Value used by this operation. Returns: The result of this operation."""
    executed.append((prompt, first_turn))
    return f"result-{len(executed)}"
agent._run_react = fake_react
result = agent.run("inspect this project and explain it")
assert len(executed) == 2, executed
assert "step 1/2" in executed[0][0].lower()
assert "step 2/2" in executed[1][0].lower()
assert result == "result-2", result
assert [step["status"] for step in agent.plan] == ["completed", "completed"]
assert executed[0][1] is True and executed[1][1] is False
print("Planning executor: steps run in order and update status")

# Test invariant or expected behavior.
simple = ReActAgent()
simple._request_plan = lambda prompt: (False, [], "")
simple_calls = []
simple._run_react = lambda prompt, first_turn=False: simple_calls.append(prompt) or "direct answer"
assert simple.run("what is 2 + 2?") == "direct answer"
assert len(simple_calls) == 1 and simple.plan == []
print("Planning router: simple request uses ReAct directly")

# Test invariant or expected behavior.
fallback = ReActAgent()
fallback._request_plan = lambda prompt: (False, [], "Planner response rejected: invalid JSON")
fallback_calls = []
fallback._run_react = lambda prompt, first_turn=False: fallback_calls.append(prompt) or "fallback answer"
assert fallback.run("do something") == "fallback answer"
assert len(fallback_calls) == 1 and fallback.plan_error
print("Planning fallback: invalid planner output does not block the request")

# Test invariant or expected behavior.
failed = ReActAgent()
failed._request_plan = lambda prompt: (True, ["step one", "step two"], "")
failure_calls = []
def failing_react(prompt, first_turn=False):
    """Perform the failing react operation. Args: prompt: Value used by this operation; first_turn: Value used by this operation. Returns: The result of this operation."""
    failure_calls.append(prompt)
    return "Max steps reached"
failed._run_react = failing_react
failure_result = failed.run("multi-step task")
assert len(failure_calls) == 1, failure_calls
assert failed.plan[0]["status"] == "failed"
assert failed.plan[1]["status"] == "pending"
assert "stopped at step 1/2" in failure_result.lower()
print("Planning failure: failed step stops the remaining plan")

# Test invariant or expected behavior.
broken_planner = ReActAgent()
class BrokenCompletions:
    """Group related behavior for brokencompletions."""
    def create(self, **kwargs):
        """Perform the create operation. Returns: The result of this operation."""
        raise ConnectionError("offline")
broken_planner.client = SimpleNamespace(
    chat=SimpleNamespace(completions=BrokenCompletions()))
assert broken_planner._request_plan("task")[0:2] == (False, [])
assert "ConnectionError" in broken_planner._request_plan("task")[2]
print("Planning request: API exception is converted to a ReAct fallback")

# Test invariant or expected behavior.
resetting = ReActAgent()
plans = iter([
    (True, ["old step one", "old step two"], ""),
    (True, ["new step one", "new step two"], ""),
])
resetting._request_plan = lambda prompt: next(plans)
resetting._run_react = lambda prompt, first_turn=False: "ok"
resetting.run("first request")
resetting.run("second request")
assert [step["text"] for step in resetting.plan] == ["new step one", "new step two"]
print("Planning state: each user request replaces the previous plan")

# Planned steps share one MAX_ROUNDS budget for the entire user request.
from config import MAX_ROUNDS
budget = ReActAgent()
budget._rounds_used = MAX_ROUNDS
assert budget._run_react("budget exhausted") == "Max steps reached"
assert budget._rounds_used == MAX_ROUNDS
print("Planning budget: exhausted request budget blocks additional ReAct rounds")

# Planner uses the same streamed thinking channel as the normal ReAct loop,
# while keeping its machine-readable JSON out of the terminal output.
from config import THINKING, REQUEST_USAGE
planning_stream_agent = ReActAgent()
planning_request = {}
planning_chunks = [
    fake_chunk(reasoning="先判断这个请求包含多个目标。"),
    fake_chunk(content='{"needs_plan": true, "steps": ["inspect", "summarize"]}',
               finish="stop", usage=usage_obj),
]
def fake_planning_create(**kwargs):
    """Perform the fake planning create operation. Returns: The result of this operation."""
    planning_request.update(kwargs)
    return iter(planning_chunks)
planning_stream_agent.client.chat.completions.create = fake_planning_create
planning_output = io.StringIO()
with contextlib.redirect_stdout(planning_output):
    planning_result = planning_stream_agent._request_plan("inspect and summarize")
assert planning_result == (True, ["inspect", "summarize"], ""), planning_result
assert "先判断这个请求" in planning_output.getvalue(), planning_output.getvalue()
assert '"needs_plan"' not in planning_output.getvalue(), planning_output.getvalue()
assert planning_request["stream"] is True
assert planning_request["extra_body"] == {"thinking": {"type": THINKING}}
assert ("stream_options" in planning_request) is REQUEST_USAGE
assert planning_stream_agent.last_usage.total_tokens == usage_obj.total_tokens
print("Planning stream: reasoning is visible, JSON is hidden and parsed")
