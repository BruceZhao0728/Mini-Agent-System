"""Structured JSONL tracing for the mini agent. Standard library only."""

import hashlib
import json
import os
import re
import sys
import threading
import time
import uuid

# Do not match usage counters such as input_tokens / total_tokens: only
# credential-shaped key names are secrets.
_SECRET_KEY = re.compile(
    r"(api[_-]?key|authorization|password|secret|^token$|"
    r"(?:access|auth|refresh|bearer|id)[_-]?token)$", re.I)
_CONTENT_KEYS = frozenset({"prompt", "content", "observation", "result"})
_HOME = os.path.expanduser("~")


def _utc_now():
    now = time.time()
    return (time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now))
            + f".{int(now * 1000) % 1000:03d}Z")


def hash_text(text):
    """Short stable hash for content that must not be stored verbatim."""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


def _clip(value, limit):
    text = str(value)
    if len(text) > limit:
        text = text[:limit] + "…"
    if _HOME:
        text = text.replace(_HOME, "~")
    return text


def _clean_value(value, limit, include_content):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if _SECRET_KEY.search(str(key)):
                cleaned[key] = "<REDACTED>"
            elif not include_content and str(key).lower() in _CONTENT_KEYS:
                continue
            else:
                cleaned[key] = _clean_value(item, limit, include_content)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_clean_value(item, limit, include_content) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _clip(value, limit)


def redact_args(args, limit=500):
    """Return a short, redacted JSON preview of tool arguments."""
    cleaned = _clean_value(args, limit, False)
    return _clip(json.dumps(cleaned, ensure_ascii=False), limit)


class NullTracer:
    """No-op tracer used when tracing is disabled."""

    enabled = False

    def new_span(self, parent=None):
        return None

    def emit(self, event, *, actor, span_id=None, parent_span_id=None, **fields):
        return None


class Tracer:
    """Append one JSON object per line, with rotation and redaction."""

    def __init__(self, path, *, max_bytes=10 * 1024 * 1024, keep=3,
                 max_field_chars=500, include_content=False):
        self.path = path
        self.trace_id = uuid.uuid4().hex
        self.max_bytes = max_bytes
        self.keep = keep
        self.max_field_chars = max_field_chars
        self.include_content = include_content
        self.enabled = True
        self._lock = threading.Lock()
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        except OSError as e:
            self.enabled = False
            print(f"⚠ tracing disabled: {type(e).__name__}: {e}", file=sys.stderr)

    def new_span(self, parent=None):
        return uuid.uuid4().hex[:16]

    def emit(self, event, *, actor, span_id=None, parent_span_id=None, **fields):
        if not self.enabled:
            return
        try:
            record = {
                "v": 1,
                "ts": _utc_now(),
                "trace_id": self.trace_id,
                "span_id": span_id or self.new_span(),
                "parent_span_id": parent_span_id,
                "event": event,
                "actor": actor,
            }
            record.update(_clean_value(fields, self.max_field_chars,
                                       self.include_content))
            line = json.dumps(record, ensure_ascii=False,
                              separators=(",", ":")) + "\n"
            data = line.encode("utf-8")
            with self._lock:
                self._rotate(len(data))
                fd = os.open(self.path,
                             os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
                try:
                    view = memoryview(data)
                    while view:
                        written = os.write(fd, view)
                        view = view[written:]
                finally:
                    os.close(fd)
        except Exception as e:
            self.enabled = False
            print(f"⚠ tracing disabled: {type(e).__name__}: {e}", file=sys.stderr)

    def _rotate(self, incoming):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size + incoming <= self.max_bytes:
            return
        for i in range(self.keep, 0, -1):
            src = self.path if i == 1 else f"{self.path}.{i - 1}"
            dst = f"{self.path}.{i}"
            if os.path.exists(src):
                os.replace(src, dst)


def get_tracer(enabled=False, path=None, **kwargs):
    if not enabled or not path:
        return NullTracer()
    return Tracer(path, **kwargs)
