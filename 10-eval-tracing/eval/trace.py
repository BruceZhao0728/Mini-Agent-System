"""Read, validate, and summarize trace JSONL files."""

import argparse
import json
import os
import re
import sys

REQUIRED = ("v", "ts", "trace_id", "span_id", "parent_span_id",
            "event", "actor")
SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|api[_-]?key\s*[:=]|"
                    r"authorization\s*[:=]|/home/[^\s\"]+)", re.I)


def load(path):
    events = []
    with open(path, encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{number}: invalid JSON: {e}") from e
    return events


def validate(events, allow_missing_parent=False, allow_open_calls=False):
    problems = []
    spans = set()
    for event in events:
        missing = [key for key in REQUIRED if key not in event]
        if missing:
            problems.append(f"event {event.get('event')!r} missing {missing}")
        if "span_id" in event:
            spans.add(event["span_id"])

    calls, results = {}, {}
    for event in events:
        parent = event.get("parent_span_id")
        if parent is not None and parent == event.get("span_id"):
            problems.append(
                f"{event.get('event')} span {event.get('span_id')} "
                f"is its own parent")
        if (parent is not None and parent not in spans
                and not allow_missing_parent):
            problems.append(
                f"{event.get('event')} span {event.get('span_id')} "
                f"has unknown parent {parent}")
        if event.get("event") == "tool_call":
            calls[event.get("tool_call_id")] = event
        if event.get("event") == "tool_result":
            results[event.get("tool_call_id")] = event

    if not allow_open_calls:
        if calls.keys() - results.keys():
            problems.append(f"tool_calls without results: {sorted(calls.keys() - results.keys())}")
        if results.keys() - calls.keys():
            problems.append(f"tool_results without calls: {sorted(results.keys() - calls.keys())}")
    for call_id in calls.keys() & results.keys():
        if calls[call_id].get("span_id") != results[call_id].get("span_id"):
            problems.append(f"tool pair {call_id} has different span ids")
    return problems


def secrets(events):
    found = []
    for event in events:
        text = json.dumps(event, ensure_ascii=False)
        match = SECRET.search(text)
        if match:
            found.append(f"{event.get('event')}: {match.group(0)[:40]}")
    return found


def summary(events):
    counts = {}
    total_in = total_out = 0
    tools = {}
    errors = 0
    for event in events:
        name = event.get("event")
        counts[name] = counts.get(name, 0) + 1
        if name == "model_request":
            total_in += event.get("input_tokens") or 0
            total_out += event.get("output_tokens") or 0
        if name == "tool_call":
            tool = event.get("tool")
            tools[tool] = tools.get(tool, 0) + 1
        if name == "error":
            errors += 1
    turns = len({event.get("turn") for event in events
                 if event.get("event") == "turn"
                 and event.get("status") == "start"})
    rounds = sum(1 for event in events
                 if event.get("event") == "round"
                 and event.get("status") == "start")
    return {
        "events": len(events),
        "turns": turns,
        "rounds": rounds,
        "model_requests": counts.get("model_request", 0),
        "input_tokens": total_in,
        "output_tokens": total_out,
        "tools": tools,
        "errors": errors,
        "counts": counts,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate a trace JSONL file.")
    parser.add_argument("--file", required=True, nargs="+")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--allow-missing-parent", action="store_true")
    parser.add_argument("--allow-open-calls", action="store_true")
    args = parser.parse_args(argv)

    failed = False
    for path in args.file:
        try:
            events = load(path)
        except Exception as e:
            print(f"{path}: {e}", file=sys.stderr)
            failed = True
            continue
        problems = validate(events, args.allow_missing_parent,
                            args.allow_open_calls)
        leaks = secrets(events)
        if args.check:
            print(f"{path}: {len(events)} events, "
                  f"{len(problems)} problems, {len(leaks)} secret hits")
            for problem in problems[:20]:
                print(f"  - {problem}")
            for leak in leaks[:20]:
                print(f"  - secret: {leak}")
            failed = failed or bool(problems or leaks)
        if args.summary:
            print(json.dumps(summary(events), ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
