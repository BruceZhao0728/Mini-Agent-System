'''History compaction for the multi-round agent — see COMPACT.md.

Pure functions: no network, no agent state, no import-time coupling to config
(the budget constants are passed in by react_agent.py, which is what makes this
module testable offline by _test.py).

One rule shapes everything here: the minimum unit of compression is a whole
(assistant message carrying tool_calls + all of its tool results) group.
Cutting inside a group — dropping a tool result, or dropping the assistant that
asked for it — produces a history the API rejects. Legal cut points are
therefore exactly two kinds of index: a turn boundary (a "user" message) and a
group start (an assistant message with tool_calls).
'''

SUMMARY_SEPARATOR = "\n\n---\n\n"

SUMMARY_HEADER = ("[Earlier conversation compressed — the summary below replaces "
                  "everything before this point]")


def truncate(text: str, limit: int) -> str:
    '''Hard-truncate from the front, keeping at most `limit` characters.

    Deliberately not utils.clip(): clip()'s head/tail arguments only take effect
    above MAX_OBS_CHARS (8000), so clip(text, 1000, 500) silently returns text
    of that length unchanged. This one always truncates. `limit <= 0` yields "".
    '''
    s = str(text)
    if limit <= 0:
        return ""
    if len(s) <= limit:
        return s
    return f"{s[:limit]}\n…[truncated {len(s) - limit} characters]…"


def describe_history(messages: list) -> list:
    '''One line per message — index, role, size — a readable map of a history.

    Used by the REPL to show what a compaction produced. Sizes are characters,
    the same honest local count as message_chars(): not a token estimate, and
    the two are consistent with each other.

    The `← summary` marker is placed at the *end* of the line on purpose: labels
    are padded to a fixed width, and CJK characters are double-width in a
    terminal, so a marker inside the padded column would misalign every line
    after it.
    '''
    lines = []
    for i, m in enumerate(messages):
        role = m.get("role", "?")
        content = str(m.get("content") or "")
        size = len(content) + len(str(m.get("reasoning_content") or ""))
        for tc in (m.get("tool_calls") or []):
            fn = tc.get("function") or {}
            size += len(str(fn.get("name") or "")) + len(str(fn.get("arguments") or ""))

        if role == "assistant" and m.get("tool_calls"):
            label = f"assistant ({len(m['tool_calls'])} tool_calls)"
        elif role == "tool":
            label = "tool result"
        else:
            label = role
        marker = "  ← summary" if content.startswith(SUMMARY_HEADER) else ""
        lines.append(f"[{i:2d}] {label:<32}{size:>9,} chars{marker}")
    return lines


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


def message_chars(messages: list) -> int:
    '''Total character count of a history.

    A character count, NOT a token estimate — this design deliberately has no
    local tokenizer (context usage comes from the API's own usage report, see
    COMPACT.md §4.3c). Used only for the "did compaction actually shrink it?"
    guard, where a character count is the honest, free, always-available proxy.

    Per-message chat-template overhead is not counted; it is small and roughly
    proportional to len(messages), which the guard watches anyway.
    '''
    total = 0
    for m in messages:
        total += len(str(m.get("content") or ""))
        total += len(str(m.get("reasoning_content") or ""))
        for tc in (m.get("tool_calls") or []):
            fn = tc.get("function") or {}
            total += len(str(fn.get("name") or ""))
            total += len(str(fn.get("arguments") or ""))
    return total


def split_history(messages: list, keep_turns: int = 2, keep_groups: int = 6,
                  keep_head: int = 1):
    '''Choose where to cut the history for compaction; None = nothing to do.

    A returned `cut` means: messages[keep_head:cut] gets summarized into one
    message, while messages[:keep_head] and messages[cut:] are kept verbatim.

    The ladder, cheapest-first in terms of what it preserves:

      1. Keep the last `keep_turns` whole turns. A turn is a safe boundary and
         the most natural one — it never splits a group.
      2. Too few turns to drop any: keep the last whole turn only. Falling
         straight through to group boundaries here would cut *into* the turn
         the user is currently asking about — dropping their question while
         keeping its answer.
      3. A single long turn (one prompt, 30 rounds): fall back to group
         boundaries. This is exactly the case that most needs compaction, and
         turn-based cutting can do nothing about it.

    keep_head is always 1: only the system prompt is protected. Everything after
    it — including a summary left by a previous compaction — stays inside the
    summarized range, so the next summary can fold it forward instead of losing
    everything the last one knew.
    '''
    if len(messages) < 3 or messages[0].get("role") != "system":
        return None

    turns = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    groups = [i for i, m in enumerate(messages)
              if m.get("role") == "assistant" and m.get("tool_calls")]

    cut = None
    if len(turns) >= keep_turns:                                # 1
        cut = turns[-keep_turns]
    if (cut is None or cut <= keep_head) and len(turns) >= 2:   # 2
        cut = turns[-1]
    if cut is None or cut <= keep_head:                         # 3
        if len(groups) >= keep_groups:
            cut = groups[-keep_groups]
        elif len(groups) >= 3:
            cut = groups[-2]
        else:
            return None

    if cut <= keep_head:
        return None
    # The range must contain at least one reply, or there is nothing to
    # summarize — the case this blocks is a range that is a lone user message,
    # e.g. a previous summary with no answer yet.
    #
    # Deliberately NOT a "must contain a tool group" test. Cut points are
    # already restricted to turn/group boundaries, so nothing structural is
    # gained by it, and it would make a plain multi-turn chat with no tool
    # calls impossible to compact at all.
    if not any(messages[i].get("role") == "assistant" for i in range(keep_head, cut)):
        return None
    return cut


def validate_history(messages: list) -> list:
    '''Check a history is legal for the chat API. Returns violations; [] = legal.

    This is the safety net for compaction. Two of the failure modes it catches
    surface as *intermittent* 400s rather than deterministic errors, so "it ran
    once without complaint" is not evidence of anything:

      * a tool_call_id with no matching tool result, or results out of order;
      * an assistant message carrying tool_calls that lost its
        reasoning_content key — the API wants it echoed back, and an empty
        string still counts, hence `is not None`.
    '''
    problems = []
    if not messages:
        return ["history is empty"]
    if messages[0].get("role") != "system":
        problems.append("messages[0] is not a system message — run()'s "
                        "first_turn = not self.messages would reset the session")

    # reasoning_content: the server sends the channel for the whole session or
    # not at all (it depends on thinking mode, not per message). So "some of the
    # tool-calling assistants have it and others do not" is exactly the
    # signature of a dropped field — and checking it this way produces no false
    # positive when the session runs with thinking disabled.
    carriers = [m for m in messages
                if m.get("role") == "assistant" and m.get("tool_calls")]
    keepers = [m for m in carriers if m.get("reasoning_content") is not None]
    if keepers and len(keepers) != len(carriers):
        problems.append(
            f"reasoning_content missing from {len(carriers) - len(keepers)} of "
            f"{len(carriers)} tool-calling assistant messages (empty string counts)")

    i, n = 0, len(messages)
    while i < n:
        m = messages[i]
        role = m.get("role")

        if role == "tool":
            problems.append(f"messages[{i}]: tool result with no preceding "
                            f"tool_calls (orphan)")
            i += 1
            continue

        if role == "assistant" and m.get("tool_calls"):
            expected = [tc.get("id") for tc in m["tool_calls"]]
            got, j = [], i + 1
            while j < n and messages[j].get("role") == "tool":
                got.append(messages[j].get("tool_call_id"))
                j += 1
            if got != expected:
                problems.append(f"messages[{i}]: tool_calls {expected} "
                                f"but results {got}")
            i = j
            continue

        i += 1
    return problems


def build_compacted(messages: list, cut: int, summary: str,
                    keep_head: int = 1) -> list:
    '''Assemble the compacted history:

        messages[:keep_head] + <summary merged into the next user message> + messages[cut:]

    The summary is merged into the following user message rather than inserted
    as a message of its own, because two consecutive user messages are rejected
    by the API ("does not support successive user or assistant messages"), and
    inserting a mid-history system message is worse: some servers ignore it
    (the summary is then silently inert, and the context is gone for nothing),
    others let it replace the real system prompt (the agent then stops emitting
    the `Final Answer:` marker that run() greps for, and burns to MAX_ROUNDS).

    Dicts are copied, not aliased. Merging writes into the following user
    message, and if it wrote into the caller's own dict a rejected candidate
    would leave the live history corrupted.
    '''
    head = [dict(m) for m in messages[:keep_head]]
    tail = [dict(m) for m in messages[cut:]]
    merged = SUMMARY_HEADER + "\n\n" + summary

    if tail and tail[0].get("role") == "user":
        tail[0]["content"] = merged + SUMMARY_SEPARATOR + str(tail[0].get("content") or "")
    else:
        tail = [{"role": "user", "content": merged}] + tail
    return head + tail


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
    react_agent writes for over-cap calls are self-describing, and the
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
