"""Transcript serialization and context display formatting."""

from .history import truncate

def chunk_text(text: str, size: int) -> list:
    '''Split text into chunks of at most `size` characters, preferring to break
    at a newline so a line is not cut in half.

    Lossless: "".join(chunk_text(t, n)) == t for any n > 0. Used to fold an
    oversized transcript through several summarization calls (COMPACT.md §4.3e);
    `size <= 0` disables splitting. Always returns at least one chunk.
    '''
    if size <= 0:
        return [text]
    out, i = [], 0
    while i < len(text):
        end = min(i + size, len(text))
        if end < len(text):
            newline = text.rfind("\n", i, end)
            if newline > i:
                end = newline + 1
        out.append(text[i:end])
        i = end
    return out or [""]


def render_transcript(messages: list, tool_chars: int = 1200,
                      reasoning_chars: int = 0) -> str:
    '''Serialize a range of messages into a readable transcript for the summarizer.

    Deliberately not json.dumps(): JSON spends tokens on keys, quotes and escaped
    newlines, and it separates a tool call from the result it produced. Tool
    results are truncated hard — the summarizer needs the gist of what came back,
    not the file contents the model read (those are already clipped to <=8000
    chars for the main conversation, a budget that is far too generous here).

    Tool errors are passed through verbatim on purpose: the synthetic
    "Error: The maximum number of tool calls per round is N" observations that
    the ReAct loop writes for over-cap calls are self-describing, and the
    summarizer should not record them as real findings.
    '''
    parts = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            parts.append("--- new user turn ---")
            parts.append(f"user: {truncate(m.get('content') or '', tool_chars)}")
        elif role == "assistant":
            content = str(m.get("content") or "").strip()
            if content:
                parts.append(f"assistant: {truncate(content, tool_chars)}")
            reasoning = m.get("reasoning_content")
            if reasoning and reasoning_chars > 0:
                parts.append("assistant (internal thinking, not user-visible): "
                             + truncate(reasoning, reasoning_chars))
            for tc in (m.get("tool_calls") or []):
                fn = tc.get("function") or {}
                parts.append(f"assistant -> called {fn.get('name')}"
                             f"({truncate(fn.get('arguments') or '', tool_chars)})")
        elif role == "tool":
            parts.append(f"tool result: {truncate(m.get('content') or '', tool_chars)}")
    return "\n".join(parts)


def format_context_line(used, window: int, messages: int,
                        warn_ratio: float = 0.70, danger_ratio: float = 0.85) -> str:
    '''One-line context-usage display for the REPL.

    `used` is the API's own total_tokens (None when nothing has been measured
    yet — a fresh session, or right after a compaction made the last number
    stale). Callers pass the ratios from config.
    '''
    if not used:
        return f"📊 Context: not measured yet ({messages} messages in history)"
    ratio = (used / window) if window else 0.0
    body = f"Context {used:,} / {window:,} ({ratio * 100:.1f}%) · {messages} messages"
    if ratio >= danger_ratio:
        return f"🔴 {body} — strongly recommend /compact"
    if ratio >= warn_ratio:
        return f"⚠️  {body} — consider /compact"
    return f"📊 {body}"


def format_compact_result(before_count: int, after_count: int,
                          before_chars: int, after_chars: int) -> str:
    '''One-line report after a successful compaction.'''
    return (f"✅ Compacted: history {before_count} → {after_count} messages "
            f"({before_chars:,} → {after_chars:,} chars); usage is re-measured on the next round")
