"""Closing-reply validation and streamed completion assertions."""

import contextlib, io
from types import SimpleNamespace
from agent import ReActAgent
from agent.helpers import _answer_warning, _message_dict

a = ReActAgent()

answer_cases = [
    # Test invariant or expected behavior.
    ("完整 ReAct 回答",            "Thought: 想好了\nFinal Answer: 42",                "stop",   False),
    ("纯聊天回答（不用格式）",      "The rewrite is complete and saved.",               "stop",   False),
    ("空回答（只有思考）",          "",                                                 "stop",   True),
    ("只有空白字符",               "  \n\t ",                                          "stop",   True),
    ("撞上输出上限",               "Final Answer: 一段没有写完的长文…",                 "length", True),
    ("开了 Thought 没收尾",        'Thought: 用户不是在问"故事讲了什么"，我已通读全文，掌', "stop",   True),
]
for desc, body, fin, expect in answer_cases:
    got = bool(_answer_warning(body, fin))
    ok8 = (got == expect)
    print(f"8a. {desc:22} finish={fin:7} -> {'警告' if got else '  — '}  {'ok' if ok8 else 'FAIL'}")
    assert ok8, f"{desc}: 期望{'有' if expect else '无'}警告，实得 {got}"

# Test invariant or expected behavior.
assert "output limit" in _answer_warning("", "length")
print("8a-2. length 与空回答同时成立 -> 报被截断（更具体的那条）")

# Test invariant or expected behavior.
def fake_chunk(content=None, reasoning=None, finish=None, usage=None, with_choices=True):
    """造一个最小可用的流块，形状照抄 SDK 的 chunk.choices[0].delta。"""
    delta = SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=None)
    choices = [SimpleNamespace(delta=delta, finish_reason=finish)] if with_choices else []
    return SimpleNamespace(choices=choices, usage=usage)

usage_obj = SimpleNamespace(prompt_tokens=10, completion_tokens=3, total_tokens=13)
# Test invariant or expected behavior.
stream_a = [fake_chunk(reasoning="让我想想"),
            fake_chunk(content="Thought: 好"),
            fake_chunk(content="了", finish="stop", usage=usage_obj)]
# Test invariant or expected behavior.
stream_b = [fake_chunk(content="Final Answer: ok", finish="length"),
            fake_chunk(usage=usage_obj, with_choices=False)]

with contextlib.redirect_stdout(io.StringIO()):     # Test invariant or expected behavior.
    m_a = a._consume_stream(iter(stream_a))
    m_b = a._consume_stream(iter(stream_b))

assert m_a.content == "Thought: 好了", repr(m_a.content)
assert m_a.reasoning_content == "让我想想" and m_a.tool_calls is None
assert m_a.finish_reason == "stop", m_a.finish_reason
assert m_a.usage.total_tokens == 13
assert m_b.finish_reason == "length" and m_b.usage.total_tokens == 13
assert _message_dict(m_b)["finish_reason"] == "length"       # Test invariant or expected behavior.
print("8b. _consume_stream：finish_reason 进 msg 也进日志；usage 两种落点都还在")

# Test invariant or expected behavior.
a.client.chat.completions.create = lambda **kw: iter([
    fake_chunk(reasoning="用户只是在打招呼"),
    fake_chunk(content="", finish="stop", usage=usage_obj),
])
shown = io.StringIO()
with contextlib.redirect_stdout(shown):
    out = a.run("打个招呼")
assert out == "", repr(out)
assert "empty reply" in shown.getvalue(), shown.getvalue()[-300:]
print("8c. run() 空回答：屏幕上出现警告，返回值仍是空串（不再静默）")

# Test invariant or expected behavior.
# Test invariant or expected behavior.
# Test invariant or expected behavior.
