"""Eval runner, fake-model routing, failure exit codes, and trace links."""

import contextlib
import copy
import io
import json
import os
import tempfile

from eval import runner
from eval.fake_model import FakeClient
from eval.recordings import RecordClient, ReplayClient, request_hash
from eval.trace import load, validate


def quiet(function, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        return function(*args, **kwargs)


# 12a. A passing case exits 0 and produces a valid trace.
assert quiet(runner.main, ["--case", "simple-final"]) == 0
case = runner.load_cases(case_id="simple-final")[0]
with tempfile.TemporaryDirectory() as trace_dir:
    result = runner.run_case(case, trace_dir)
    assert result.passed, result.failures
    events = load(os.path.join(trace_dir, "simple-final.jsonl"))
    assert validate(events) == [], validate(events)
    assert any(event["event"] == "tool_result" for event in events) is False
    assert any(event["event"] == "model_request" for event in events)

# 12b. A broken expectation exits non-zero, and it is a case failure (1),
# not a harness error (2).
broken = copy.deepcopy(case)
broken["expect"]["final_contains"] = ["no-such-text"]
with tempfile.TemporaryDirectory() as trace_dir:
    result = runner.run_case(broken, trace_dir)
    assert not result.passed and not result.harness_error, result
assert quiet(runner.main, ["--suite", "does-not-exist"]) == 2

# 12c. FakeClient routes planner / executor / summarizer correctly.
planner = FakeClient(case)
planner_chunks = list(planner.create(
    model="m", messages=[{"role": "system", "content": "plan"}],
    stream=True))
assert len(planner_chunks) == 2
assert "needs_plan" in planner_chunks[0].choices[0].delta.content

executor = FakeClient(case)
executor_chunks = list(executor.create(
    model="m", messages=[{"role": "system", "content": "exec"}],
    tools=[], stream=True))
assert any(chunk.choices and chunk.choices[0].delta.content == "Final Answer: 4"
           for chunk in executor_chunks)

summarizer = FakeClient(case)
response = summarizer.create(model="m", messages=[], stream=False)
assert response.choices[0].message.content

# 12d. A tool case produces matched tool_call / tool_result spans.
tool_case = runner.load_cases(case_id="single-tool-read")[0]
with tempfile.TemporaryDirectory() as trace_dir:
    result = runner.run_case(tool_case, trace_dir)
    assert result.passed, result.failures
    events = load(os.path.join(trace_dir, "single-tool-read.jsonl"))
    assert validate(events) == [], validate(events)
    calls = [e for e in events if e["event"] == "tool_call"]
    results = [e for e in events if e["event"] == "tool_result"]
    assert len(calls) == 1 and len(results) == 1
    assert calls[0]["span_id"] == results[0]["span_id"]
    assert calls[0]["tool_call_id"] == results[0]["tool_call_id"]

# 12e. Recording keys are stable and stale requests fail loudly.
base = {"model": "m", "messages": [{"role": "user", "content": "x"}],
        "tools": [], "stream": True}
assert request_hash(base) == request_hash(dict(base))
changed = {"model": "m", "messages": [{"role": "user", "content": "y"}],
           "tools": [], "stream": True}
assert request_hash(base) != request_hash(changed)
with tempfile.TemporaryDirectory() as trace_dir:
    replay = ReplayClient(os.path.join(trace_dir, "missing.jsonl"))
    try:
        replay.create(**base)
    except RuntimeError:
        pass
    else:
        raise AssertionError("missing recording did not fail")

# 12f. Record then replay a streamed response without any network.
with tempfile.TemporaryDirectory() as trace_dir:
    recording = os.path.join(trace_dir, "recording.jsonl")
    recorder = RecordClient(FakeClient(case), recording, case["id"])
    original = list(recorder.create(
        model="m", messages=[{"role": "user", "content": "x"}],
        tools=[], stream=True))
    assert os.path.exists(recording)
    replay = ReplayClient(recording)
    replayed = list(replay.create(
        model="m", messages=[{"role": "user", "content": "x"}],
        tools=[], stream=True))
    assert any(chunk.choices and chunk.choices[0].delta.content == "Final Answer: 4"
               for chunk in replayed)

print("12. Eval harness: pass/fail/error / routing / trace pairing / record-replay")
