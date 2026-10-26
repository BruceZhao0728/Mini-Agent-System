"""Sanitized record/replay of model responses.

Only responses are stored. Each response is keyed by a hash of the request
(model + messages + tools + thinking + stream + max_tokens), so a prompt or
tool-schema change makes the recording stale instead of silently passing.
"""

import datetime
import hashlib
import json
import os
import threading
from types import SimpleNamespace


def request_hash(kwargs):
    payload = {
        "model": kwargs.get("model"),
        "messages": kwargs.get("messages"),
        "tools": kwargs.get("tools"),
        "stream": kwargs.get("stream"),
        "max_tokens": kwargs.get("max_tokens"),
        "thinking": (kwargs.get("extra_body") or {}).get("thinking"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def _chunk_to_dict(chunk):
    record = {"with_choices": bool(getattr(chunk, "choices", None)),
              "usage": None}
    usage = getattr(chunk, "usage", None)
    if usage is not None:
        record["usage"] = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
    if record["with_choices"]:
        choice = chunk.choices[0]
        delta = choice.delta
        tool_calls = []
        for tc in (getattr(delta, "tool_calls", None) or []):
            tool_calls.append({
                "index": tc.index,
                "id": getattr(tc, "id", None),
                "name": getattr(getattr(tc, "function", None), "name", None),
                "arguments": getattr(getattr(tc, "function", None),
                                     "arguments", None),
            })
        record.update({
            "content": getattr(delta, "content", None),
            "reasoning_content": getattr(delta, "reasoning_content", None),
            "finish_reason": getattr(choice, "finish_reason", None),
            "tool_calls": tool_calls,
        })
    return record


def _dict_to_chunk(record):
    delta = SimpleNamespace(
        content=record.get("content"),
        reasoning_content=record.get("reasoning_content"),
        tool_calls=None)
    if record.get("tool_calls"):
        delta.tool_calls = [SimpleNamespace(
            index=tc["index"], id=tc["id"], type="function",
            function=SimpleNamespace(name=tc["name"],
                                     arguments=tc["arguments"]))
            for tc in record["tool_calls"]]
    choices = ([SimpleNamespace(delta=delta,
                                finish_reason=record.get("finish_reason"))]
               if record.get("with_choices") else [])
    usage = record.get("usage")
    if usage:
        usage = SimpleNamespace(**usage)
    return SimpleNamespace(choices=choices, usage=usage)


class RecordClient:
    """Wrap a real client, tee streamed responses into a JSONL recording."""

    def __init__(self, inner, path, case_id, meta=None):
        self.inner = inner
        self.path = path
        self.case_id = case_id
        self.requests = []
        self.max_retries = getattr(inner, "max_retries", 0)
        self._lock = threading.Lock()
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create))
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        header = {"type": "meta", "case_id": case_id,
                  "date": datetime.date.today().isoformat()}
        header.update(meta or {})
        self._append(header)

    def _append(self, record):
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if kwargs.get("stream"):
            return self._stream(kwargs)
        response = self.inner.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        self._append({"type": "response", "request_hash": request_hash(kwargs),
                      "stream": False, "content": content})
        return response

    def _stream(self, kwargs):
        stream = self.inner.chat.completions.create(**kwargs)
        chunks = []
        for chunk in stream:
            chunks.append(_chunk_to_dict(chunk))
            yield chunk
        self._append({"type": "response", "request_hash": request_hash(kwargs),
                      "stream": True, "chunks": chunks})


class ReplayClient:
    """Replay responses recorded by RecordClient; stale requests fail loudly."""

    def __init__(self, path):
        self.path = path
        self.requests = []
        self.missing = []
        self.max_retries = 0
        self.responses = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if record.get("type") == "response":
                        self.responses[record["request_hash"]] = record
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        key = request_hash(kwargs)
        record = self.responses.get(key)
        if record is None:
            self.missing.append(key)
            raise RuntimeError(
                "recording missing or stale for this request "
                "(model/prompt/tools changed, or --record was not run)")
        if record.get("stream"):
            return iter([_dict_to_chunk(chunk) for chunk in record["chunks"]])
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=record.get("content", "")))])
