"""Scripted model and fake tools used by the offline eval harness."""

import json
from types import SimpleNamespace


def fake_chunk(content=None, reasoning=None, finish=None, usage=None,
               with_choices=True):
    """Build the minimal chunk shape consumed by ExecutionMixin._consume_stream."""
    delta = SimpleNamespace(content=content, reasoning_content=reasoning,
                            tool_calls=None)
    choices = ([SimpleNamespace(delta=delta, finish_reason=finish)]
               if with_choices else [])
    return SimpleNamespace(choices=choices, usage=usage)


def tool_call_chunk(index, call_id, name, arguments):
    """One tool-call fragment; _consume_stream joins fragments by index."""
    tc = SimpleNamespace(
        index=index, id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=arguments))
    delta = SimpleNamespace(content=None, reasoning_content=None,
                            tool_calls=[tc])
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=None)],
        usage=None)


def usage_obj(prompt_tokens=10, completion_tokens=3):
    return SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens)


class FakeClient:
    """A scripted replacement for OpenAI's chat.completions client.

    Routing:
      stream=True  and no `tools`  -> planner
      stream=True  and has `tools` -> ReAct executor
      stream=False                -> summarizer

    The executor consumes one script entry per model round. A `tool_calls`
    entry produces one round with several calls; a `tool_call` entry produces
    one round with one call; `final` closes the loop.
    """

    def __init__(self, case):
        self.case = case
        self.requests = []
        self.script = list(case.get("script", []))
        self.max_retries = 0
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        stream = bool(kwargs.get("stream"))
        if stream and "tools" not in kwargs:
            return iter(self._planner_stream())
        if stream and "tools" in kwargs:
            return iter(self._executor_stream())
        return self._summarizer_response()

    def _planner_stream(self):
        plan = self.case.get("plan", {"needs_plan": False, "steps": []})
        content = plan["raw"] if "raw" in plan else json.dumps(
            plan, ensure_ascii=False)
        yield fake_chunk(content=content)
        yield fake_chunk(content="", finish="stop", usage=usage_obj())

    def _executor_stream(self):
        if not self.script:
            raise RuntimeError("script exhausted: no more model responses")
        step = self.script.pop(0)
        kind = step.get("type")
        if kind == "tool_call":
            yield tool_call_chunk(
                0, step.get("id", self._call_id()), step["name"],
                step.get("arguments_raw",
                         json.dumps(step.get("arguments", {}),
                                    ensure_ascii=False)))
            yield fake_chunk(content="", finish="tool_calls",
                             usage=usage_obj())
            return
        if kind == "tool_calls":
            for index, call in enumerate(step.get("calls", [])):
                yield tool_call_chunk(
                    index, call.get("id", self._call_id()), call["name"],
                    call.get("arguments_raw",
                             json.dumps(call.get("arguments", {}),
                                        ensure_ascii=False)))
            yield fake_chunk(content="", finish="tool_calls",
                             usage=usage_obj())
            return
        if kind == "final":
            yield fake_chunk(content=step.get("content", ""))
            yield fake_chunk(content="", finish="stop", usage=usage_obj())
            return
        if kind == "raise":
            raise RuntimeError(step.get("error", "scripted failure"))
        raise RuntimeError(f"unknown script step: {kind!r}")

    def _summarizer_response(self):
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(
                content=self.case.get("summary", "Summary of earlier conversation.")))])

    def _call_id(self):
        return f"call_{len(self.requests)}"
