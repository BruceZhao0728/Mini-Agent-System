"""Tracer schema, concurrency, redaction, rotation, and failure containment."""

import contextlib
import io
import json
import os
import tempfile
import threading

from tracing import Tracer, NullTracer, hash_text, redact_args

REQUIRED = ("v", "ts", "trace_id", "span_id", "parent_span_id",
            "event", "actor")


def _lines(path):
    with open(path, encoding="utf-8") as f:
        return [line for line in f if line.strip()]


with tempfile.TemporaryDirectory() as tmp:
    # 11a. Disabled tracer is a true no-op.
    null = NullTracer()
    assert null.new_span() is None
    null.emit("turn", actor="test", status="start")
    assert not os.listdir(tmp)

    # 11b. One event is one parseable JSON line with the common schema.
    path = os.path.join(tmp, "one.jsonl")
    tracer = Tracer(path)
    tracer.emit("turn", actor="test", turn=1, status="start",
                prompt_hash=hash_text("hello"))
    event = json.loads(_lines(path)[0])
    assert all(key in event for key in REQUIRED), event
    assert event["event"] == "turn" and event["actor"] == "test"
    assert event["trace_id"] and event["span_id"]

    # 11c. Concurrent writers produce whole lines and unique spans.
    concurrent = os.path.join(tmp, "concurrent.jsonl")
    tracer = Tracer(concurrent)
    barrier = threading.Barrier(4, timeout=2)

    def worker(number):
        barrier.wait()
        for index in range(50):
            tracer.emit("tool_call", actor="test",
                        span_id=tracer.new_span(),
                        tool_call_id=f"{number}-{index}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    events = [json.loads(line) for line in _lines(concurrent)]
    assert len(events) == 200, len(events)
    assert len({event["span_id"] for event in events}) == 200

    # 11d. Credential-shaped keys are redacted; usage counters are not.
    preview = redact_args({
        "api_key": "sk-1234567890",
        "authorization": "Bearer x",
        "path": "/home/bruce/project/a.txt",
        "total_tokens": 13,
        "nested": {"password": "p", "secret": "s"},
    })
    assert "sk-1234567890" not in preview, preview
    assert "/home/bruce" not in preview, preview
    assert preview.count("<REDACTED>") >= 4, preview
    assert "13" in preview, preview

    # 11e. Rotation keeps whole lines; no line is split across files.
    rotated = os.path.join(tmp, "rotated.jsonl")
    tracer = Tracer(rotated, max_bytes=250, keep=50)
    for index in range(20):
        tracer.emit("turn", actor="test", turn=index, status="start",
                    span_id=f"{index:016x}")
    files = [rotated] + [f"{rotated}.{i}" for i in range(1, 51)
                         if os.path.exists(f"{rotated}.{i}")]
    total = 0
    for filename in files:
        for line in _lines(filename):
            json.loads(line)
            total += 1
    assert total == 20, total
    assert os.path.exists(rotated + ".1"), "rotation did not happen"

    # 11f. An unwritable path disables tracing instead of raising.
    with tempfile.NamedTemporaryFile() as blocker:
        with contextlib.redirect_stderr(io.StringIO()):
            broken = Tracer(os.path.join(blocker.name, "trace.jsonl"))
        assert broken.enabled is False
        broken.emit("turn", actor="test", status="start")

print("11. Tracer: no-op / schema / concurrency / redaction / rotation / failure")
