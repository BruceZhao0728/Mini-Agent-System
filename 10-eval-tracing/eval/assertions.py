"""Assertion layers for offline eval cases.

Each check returns a list of human-readable failures. The runner turns an
empty list into PASS and a non-empty list into FAIL.
"""

import json
import re


def _tool_calls(messages):
    calls = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for tc in message.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except (KeyError, json.JSONDecodeError):
                args = {}
            calls.append({
                "name": tc["function"]["name"],
                "arguments": args,
                "id": tc.get("id"),
            })
    return calls


def _subset(expected, actual):
    return all(actual.get(key) == value for key, value in expected.items())


def _check_request_contract(requests, expect):
    failures = []
    contract = expect.get("request") or {}
    planners = [r for r in requests if r.get("stream") and "tools" not in r]
    executors = [r for r in requests if r.get("stream") and "tools" in r]

    if "planner_called" in contract:
        if bool(planners) != bool(contract["planner_called"]):
            failures.append(
                f"request.planner_called expected {contract['planner_called']}, "
                f"got {bool(planners)}")
    if "system_prompt_contains" in contract:
        if not executors:
            failures.append("request.system_prompt_contains: no executor request")
        else:
            system = executors[-1]["messages"][0].get("content") or ""
            for needle in contract["system_prompt_contains"]:
                if needle not in system:
                    failures.append(
                        f"system prompt missing {needle!r}")
    if "tool_schema_names" in contract:
        if not executors:
            failures.append("request.tool_schema_names: no executor request")
        else:
            names = {tool["function"]["name"] for tool in executors[-1]["tools"]}
            for name in contract["tool_schema_names"]:
                if name not in names:
                    failures.append(f"tool schema missing {name!r}")
    return failures


def _check_trace(trace_events, expect):
    failures = []
    if not trace_events:
        return failures

    spans = {e.get("span_id") for e in trace_events}
    calls = {e.get("tool_call_id") for e in trace_events
             if e.get("event") == "tool_call"}
    results = {e.get("tool_call_id") for e in trace_events
               if e.get("event") == "tool_result"}

    if expect.get("trace_parent_links", True):
        for event in trace_events:
            parent = event.get("parent_span_id")
            if parent is not None and parent not in spans:
                failures.append(
                    f"trace: {event.get('event')} has unknown parent {parent}")
                break
        if calls - results:
            failures.append(f"trace: calls without results: {sorted(calls - results)}")
        if results - calls:
            failures.append(f"trace: results without calls: {sorted(results - calls)}")

    counts = expect.get("trace_event_counts")
    if counts:
        actual = {}
        for event in trace_events:
            actual[event["event"]] = actual.get(event["event"], 0) + 1
        for name, wanted in counts.items():
            if actual.get(name, 0) != wanted:
                failures.append(
                    f"trace event {name}: expected {wanted}, got {actual.get(name, 0)}")
    return failures


def check_case(agent, case, result, requests, trace_events=None):
    """Return a list of failures; [] means the case passed."""
    failures = []
    expect = case.get("expect", {})
    trace_events = trace_events or []

    # 1. Structure: the history protocol must hold.
    if expect.get("history_valid", True):
        from context import validate_history
        problems = validate_history(agent.messages)
        if problems:
            failures.append("history invalid: " + "; ".join(problems[:3]))

    # 2. Trajectory: tool names, argument subsets, call count.
    calls = _tool_calls(agent.messages)
    if "tool_sequence" in expect:
        actual = [call["name"] for call in calls]
        if actual != expect["tool_sequence"]:
            failures.append(
                f"tool_sequence expected {expect['tool_sequence']}, got {actual}")
    if "tool_args" in expect:
        for index, wanted in enumerate(expect["tool_args"]):
            if index >= len(calls):
                failures.append(f"tool_args: missing call #{index + 1}")
            elif not _subset(wanted, calls[index]["arguments"]):
                failures.append(
                    f"tool_args #{index + 1}: expected subset {wanted}, "
                    f"got {calls[index]['arguments']}")
    if "tool_call_count" in expect and len(calls) != expect["tool_call_count"]:
        failures.append(
            f"tool_call_count expected {expect['tool_call_count']}, got {len(calls)}")

    tool_messages = [m for m in agent.messages if m.get("role") == "tool"]
    contents = [m.get("content") or "" for m in tool_messages]
    if expect.get("no_tool_errors"):
        bad = [c[:120] for c in contents if c.startswith("Error:")]
        if bad:
            failures.append(f"tool errors: {bad}")
    if "tool_results_contain" in expect:
        for needle in expect["tool_results_contain"]:
            if not any(needle in content for content in contents):
                failures.append(f"no tool result contains {needle!r}")

    # 3. Request contract: catches prompt / tool-schema regressions, which a
    # scripted model cannot notice by itself.
    failures.extend(_check_request_contract(requests, expect))

    # 4. Result: prefer contains / regex over exact equality.
    if "final_contains" in expect:
        for needle in expect["final_contains"]:
            if needle not in (result or ""):
                failures.append(f"final answer missing {needle!r}")
    if "final_regex" in expect:
        if not re.search(expect["final_regex"], result or "", re.S):
            failures.append(f"final answer did not match {expect['final_regex']!r}")
    if "final_equals" in expect and result != expect["final_equals"]:
        failures.append(f"final answer expected exactly {expect['final_equals']!r}")

    # 5. Cost: rounds, model requests, tokens.
    if "max_rounds" in expect and agent._rounds_used > expect["max_rounds"]:
        failures.append(
            f"rounds {agent._rounds_used} > max_rounds {expect['max_rounds']}")
    if "max_model_requests" in expect and len(requests) > expect["max_model_requests"]:
        failures.append(
            f"model requests {len(requests)} > max_model_requests "
            f"{expect['max_model_requests']}")
    if "max_total_tokens" in expect:
        total = sum(event.get("total_tokens") or 0 for event in trace_events
                    if event.get("event") == "model_request")
        if total > expect["max_total_tokens"]:
            failures.append(
                f"total tokens {total} > max_total_tokens "
                f"{expect['max_total_tokens']}")

    # 6. Trace invariants.
    failures.extend(_check_trace(trace_events, expect))
    return failures
