"""Offline eval runner.

Usage:
    python3 eval/runner.py --suite smoke
    python3 eval/runner.py --case simple-final
    python3 eval/runner.py --json
    python3 eval/runner.py --case simple-final --record
    python3 eval/runner.py --case simple-final --replay
"""

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import traceback
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from io import StringIO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# These must be set before the first import of config / agent: config captures
# them by value at import time.
os.environ.setdefault("MINI_AGENT_LOG", "0")
os.environ.setdefault("MINI_AGENT_TRACE", "1")

from eval.assertions import check_case
from eval.fake_model import FakeClient

CASES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases")


@dataclass
class CaseResult:
    case_id: str
    passed: bool = False
    harness_error: bool = False
    failures: list = field(default_factory=list)
    rounds: int = 0
    tools: int = 0
    tokens: int = 0
    duration_ms: float = 0.0
    trace_path: str = ""


def load_cases(suite=None, case_id=None):
    cases = []
    for name in sorted(os.listdir(CASES_DIR)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(CASES_DIR, name), encoding="utf-8") as f:
            case = json.load(f)
        if case_id and case.get("id") != case_id:
            continue
        if suite and case.get("suite") != suite:
            continue
        cases.append(case)
    return cases


def make_tool(name, spec, shared_barrier=None):
    """Turn one JSON tool spec into a callable; keep it thread-safe."""
    if isinstance(spec, str):
        return lambda **_: spec
    if not isinstance(spec, dict):
        return lambda **_: ""
    mode = spec.get("mode")
    if mode == "shared_barrier" and shared_barrier is not None:
        result = spec.get("result", "ok")

        def shared_tool(**_):
            shared_barrier.wait()
            return result
        return shared_tool
    if mode == "barrier":
        barrier = threading.Barrier(
            int(spec.get("parties", 2)), timeout=float(spec.get("timeout", 2)))
        result = spec.get("result", "ok")

        def barrier_tool(**_):
            barrier.wait()
            return result
        return barrier_tool
    if "error" in spec:
        error = spec["error"]

        def raising_tool(**_):
            raise RuntimeError(error)
        return raising_tool
    result = spec.get("result", "")

    def fixed_tool(**_):
        return result
    return fixed_tool


def read_trace(path):
    if not os.path.exists(path):
        return []
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return events


def run_case(case, trace_dir, record=False, replay=False):
    started = time.monotonic()
    trace_path = os.path.join(trace_dir, f"{case['id']}.jsonl")
    if os.path.exists(trace_path):
        os.remove(trace_path)
    rec_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "recordings",
        f"{case['id']}.jsonl")

    from agent import ReActAgent
    from tools import TOOLS, TOOL_META
    from tracing import Tracer, NullTracer
    import agent.react_loop as react_loop
    import agent.execution as execution
    import utils

    added = []
    old_tools, old_meta = {}, {}
    old_limits = (react_loop.MAX_ROUNDS, execution.MAX_TOOL_CALLS_PER_ROUND,
                  execution.MAX_OBS_CHARS, utils.MAX_OBS_CHARS)
    limits = case.get("limits") or {}
    barrier_spec = case.get("shared_barrier")
    shared_barrier = (threading.Barrier(
        int(barrier_spec.get("parties", 2)),
        timeout=float(barrier_spec.get("timeout", 2)))
        if barrier_spec else None)

    try:
        for name, spec in (case.get("tools") or {}).items():
            old_tools[name] = TOOLS.get(name)
            old_meta[name] = TOOL_META.get(name)
            TOOLS[name] = make_tool(name, spec, shared_barrier)
            TOOL_META[name] = {
                "parallel_safe": bool(spec.get("parallel_safe", False))
                if isinstance(spec, dict) else False}
            added.append(name)

        react_loop.MAX_ROUNDS = int(limits.get("max_rounds", old_limits[0]))
        execution.MAX_TOOL_CALLS_PER_ROUND = int(
            limits.get("max_tool_calls", old_limits[1]))
        execution.MAX_OBS_CHARS = int(limits.get("max_obs_chars", old_limits[2]))
        utils.MAX_OBS_CHARS = execution.MAX_OBS_CHARS

        if record:
            from openai import OpenAI
            from config import (API_KEY, BASE_URL, MAX_API_RETRIES,
                                API_TIMEOUT, MODEL, THINKING)
            from tools import TOOLS_DESC
            from tracing import hash_text
            from eval.recordings import RecordClient
            if API_KEY == "sk-your-key-here":
                raise RuntimeError("--record needs DEEPSEEK_API_KEY")
            os.makedirs(os.path.dirname(rec_path), exist_ok=True)
            inner = OpenAI(api_key=API_KEY, base_url=BASE_URL,
                           max_retries=MAX_API_RETRIES,
                           timeout=API_TIMEOUT)
            meta = {
                "model": MODEL, "thinking": THINKING,
                "tools_hash": hash_text(json.dumps(
                    TOOLS_DESC, ensure_ascii=False, sort_keys=True)),
                "prompt_hash": hash_text(case["prompt"]),
            }
            client = RecordClient(inner, rec_path, case["id"], meta=meta)
        elif replay:
            from eval.recordings import ReplayClient
            client = ReplayClient(rec_path)
        else:
            client = FakeClient(case)

        tracer = (NullTracer() if os.environ.get("MINI_AGENT_TRACE") == "0"
                  else Tracer(trace_path))
        agent = ReActAgent(client=client, tracer=tracer)

        with redirect_stdout(StringIO()):
            result = agent.run(case["prompt"])

        # The agent converts model errors into strings; a missing/stale
        # recording is a harness error, not a case failure.
        if replay and getattr(client, "missing", None):
            raise RuntimeError(
                f"recording missing or stale for {len(client.missing)} request(s)")

        events = read_trace(trace_path)
        failures = check_case(agent, case, result, client.requests, events)
        tools = sum(len(m.get("tool_calls") or []) for m in agent.messages
                    if m.get("role") == "assistant")
        tokens = sum(e.get("total_tokens") or 0 for e in events
                     if e.get("event") == "model_request")
        return CaseResult(
            case_id=case["id"], passed=not failures, failures=failures,
            rounds=agent._rounds_used, tools=tools, tokens=tokens,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
            trace_path=trace_path)
    except Exception as e:
        return CaseResult(
            case_id=case.get("id", "<unknown>"), harness_error=True,
            failures=[f"{type(e).__name__}: {e}",
                      traceback.format_exc(limit=2)],
            duration_ms=round((time.monotonic() - started) * 1000, 1),
            trace_path=trace_path)
    finally:
        for name in added:
            if old_tools[name] is None:
                TOOLS.pop(name, None)
            else:
                TOOLS[name] = old_tools[name]
            if old_meta[name] is None:
                TOOL_META.pop(name, None)
            else:
                TOOL_META[name] = old_meta[name]
        (react_loop.MAX_ROUNDS, execution.MAX_TOOL_CALLS_PER_ROUND,
         execution.MAX_OBS_CHARS, utils.MAX_OBS_CHARS) = old_limits


def print_table(results):
    print(f"{'id':22} {'result':7} {'rounds':>6} {'tools':>5} "
          f"{'tokens':>7} {'ms':>7}")
    for result in results:
        status = ("ERROR" if result.harness_error
                  else "PASS" if result.passed else "FAIL")
        print(f"{result.case_id:22} {status:7} {result.rounds:>6} "
              f"{result.tools:>5} {result.tokens:>7} {result.duration_ms:>7.1f}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run offline eval cases.")
    parser.add_argument("--suite", help="smoke / failure / live")
    parser.add_argument("--case", help="one case id")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--record", action="store_true",
                        help="run against the real API and save responses")
    parser.add_argument("--replay", action="store_true",
                        help="replay a saved recording offline")
    args = parser.parse_args(argv)
    if args.record and args.replay:
        print("--record and --replay are mutually exclusive", file=sys.stderr)
        return 2

    try:
        cases = load_cases(args.suite, args.case)
    except Exception as e:
        print(f"harness error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if not cases:
        print("no cases matched", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="mini_agent_eval_") as trace_dir:
        results = [run_case(case, trace_dir, args.record, args.replay)
                   for case in cases]
        # Print while the trace directory still exists, so trace_path is usable.
        if args.json:
            print(json.dumps([result.__dict__ for result in results],
                             ensure_ascii=False, indent=2))
        else:
            print_table(results)
            for result in results:
                if result.failures:
                    print(f"\n{result.case_id}:")
                    for failure in result.failures:
                        print(f"  - {failure}")

    if any(result.harness_error for result in results):
        return 2
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
