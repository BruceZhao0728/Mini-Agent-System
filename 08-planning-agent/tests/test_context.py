"""Conversation-history validation and compaction assertions."""

import context as ctx
from agent import ReActAgent
from utils import clip

def mk(turns, groups=2):
    """Build realistic history with a user, tool-call groups, and a closing assistant per turn."""
    m = [{"role": "system", "content": "SYS"}]
    for t in range(turns):
        m.append({"role": "user", "content": f"question {t} " + "u" * 200})
        for g in range(groups):
            cid = f"t{t}g{g}"
            m.append({"role": "assistant", "content": f"thinking {t}-{g}",
                      "reasoning_content": "",
                      "tool_calls": [{"id": cid, "type": "function",
                                      "function": {"name": "read_file",
                                                   "arguments": '{"path":"a.py"}'}}]})
            m.append({"role": "tool", "tool_call_id": cid, "content": "result " * 100})
        m.append({"role": "assistant", "content": f"Final answer {t}",
                  "reasoning_content": ""})
    return m


# Test invariant or expected behavior.
assert len(ctx.truncate("x" * 3000, 1000)) < 1100
assert ctx.truncate("abc", 10) == "abc"
assert ctx.truncate("abc", 0) == ""       # Test invariant or expected behavior.
assert clip("x" * 3000, 1000, 500) == "x" * 3000, "clip 行为变了 —— COMPACT.md §1.5 要更新"
print("6a. truncate 生效；clip(x,1000,500) 对 3000 字符仍原样返回（已知行为，见 §1.5）")

# Test invariant or expected behavior.
long_text = "\n".join(f"line {i} " + "x" * 30 for i in range(50))
assert "".join(ctx.chunk_text(long_text, 200)) == long_text
assert all(len(c) <= 200 for c in ctx.chunk_text(long_text, 200))
assert ctx.chunk_text("", 10) == [""]
assert ctx.chunk_text(long_text, 0) == [long_text]
print("6b. chunk_text 无损且不超长")

# Test invariant or expected behavior.
assert ctx.split_history([{"role": "system", "content": "S"}]) is None
assert ctx.split_history(mk(1)[:2]) is None
assert ctx.split_history(mk(1)) is None            # Test invariant or expected behavior.
assert ctx.split_history(mk(2)) == 7               # Test invariant or expected behavior.
assert ctx.split_history(mk(3)) == 7               # Test invariant or expected behavior.
assert ctx.split_history(mk(1, groups=10)) == 10   # Test invariant or expected behavior.
for msgs in (mk(2), mk(3), mk(5), mk(1, groups=10)):
    cut = ctx.split_history(msgs)
    legal = {i for i, m in enumerate(msgs)
             if m.get("role") == "user"
             or (m.get("role") == "assistant" and m.get("tool_calls"))}
    assert cut in legal, f"切点 {cut} 既不是轮边界也不是组起点"
print("6c. split_history：6 个边界用例 + 切点只落在合法位置")

# Test invariant or expected behavior.
# Test invariant or expected behavior.
chat_only = [{"role": "system", "content": "SYS"}]
for t in range(4):
    chat_only.append({"role": "user", "content": f"问题 {t}" + "u" * 300})
    chat_only.append({"role": "assistant", "content": f"回答 {t}" + "a" * 300,
                      "reasoning_content": ""})
assert ctx.split_history(chat_only) == 5, ctx.split_history(chat_only)
print("6c. 纯对话历史（无工具）也能切：cut =", ctx.split_history(chat_only))

# Test invariant or expected behavior.
good = mk(2)
assert ctx.validate_history(good) == [], ctx.validate_history(good)
i0 = next(i for i, m in enumerate(good) if m.get("tool_calls"))
illegal = {
    "孤儿 tool 结果": good[:3] + [{"role": "tool", "tool_call_id": "zz", "content": "x"}] + good[3:],
    "少了某个结果":    [m for i, m in enumerate(good) if i != 3],
    "丢了 reasoning": [({k: v for k, v in m.items() if k != "reasoning_content"} if i == i0 else m)
                       for i, m in enumerate(good)],
    "首条不是 system": [{"role": "user", "content": "x"}] + good[1:],
    "结果顺序错乱":    good[:2] + [good[3], good[2]] + good[4:],
}
for desc, msgs in illegal.items():
    v = ctx.validate_history(msgs)
    assert v, f"{desc} 居然通过了校验"
    print(f"6d. {desc:14} -> {v[0][:56]}")
# Test invariant or expected behavior.
no_thinking = [{k: v for k, v in m.items() if k != "reasoning_content"} for m in good]
assert ctx.validate_history(no_thinking) == [], ctx.validate_history(no_thinking)
print("6d. 反例：thinking 关闭的历史不误报")

# Test invariant or expected behavior.
checked = 0
for turns in (1, 2, 3, 5):
    for groups in (1, 2, 3, 8):
        for keep in (1, 2, 3):
            msgs = mk(turns, groups)
            cut = ctx.split_history(msgs, keep_turns=keep)
            if cut is None:
                continue
            checked += 1
            new = ctx.build_compacted(msgs, cut, "SUMMARY " * 40)
            assert ctx.validate_history(new) == [], (turns, groups, keep, ctx.validate_history(new))
            assert ctx.message_chars(new) < ctx.message_chars(msgs), (turns, groups, keep)
print(f"6e. 属性测试 {checked} 个组合：全部合法且严格变小")

# Test invariant or expected behavior.
msgs = mk(3)
cut = ctx.split_history(msgs)
new = ctx.build_compacted(msgs, cut, "THE SUMMARY")
assert new[0]["role"] == "system"
assert new[1]["role"] == "user" and "THE SUMMARY" in new[1]["content"]
assert "question 1" in new[1]["content"]      # Test invariant or expected behavior.
for i in range(1, len(new)):
    prev_role, role = new[i - 1]["role"], new[i]["role"]
    assert not (prev_role == role and role in ("user", "assistant")), f"messages[{i}] 连续同角色"
before = msgs[cut]["content"]
ctx.build_compacted(msgs, cut, "SHOULD NOT LEAK")
assert msgs[cut]["content"] == before, "build_compacted 改动了调用方的历史"
print("6f. 组装：合并进 user、无连续同角色、不污染原历史")

# Test invariant or expected behavior.
h = mk(6)
h = ctx.build_compacted(h, ctx.split_history(h), "SUMMARY-ONE")
c2 = ctx.split_history(h)
assert "SUMMARY-ONE" in ctx.render_transcript(h[1:c2], tool_chars=1200), \
    "上一次的摘要没进下一次总结的输入 —— 会失忆"
print("6g. 折叠：第二次压缩的输入里含第一次的摘要")

# Test invariant or expected behavior.
ag = ReActAgent()
ag.messages = mk(3)
ag.task_text = "orig"
snapshot = [dict(m) for m in ag.messages]
for desc, stub in (("摘要为空", lambda *a, **k: ""),
                   ("总结调用抛异常", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("boom")))):
    ag._summarize_once = stub
    changed, msg = ag.compact()
    assert changed is False, f"{desc}：居然报告成功了"
    assert len(ag.messages) == len(snapshot), f"{desc}：历史被改动了"
    assert ag.messages[0]["role"] == "system"
    print(f"6h. {desc:16} -> {msg[:54]}")
# Test invariant or expected behavior.
ag2 = ReActAgent()
ag2.messages = mk(1)
changed, msg = ag2.compact()
assert changed is False and "Nothing to compact" in msg, msg
print(f"6h. 无可压缩内容      -> {msg}")

# Test invariant or expected behavior.
shown = ctx.describe_history(new)
assert len(shown) == len(new), (len(shown), len(new))
assert sum(1 for line in shown if "← summary" in line) == 1, shown
assert sum(1 for line in shown if "tool_calls" in line) == \
       sum(1 for m in new if m.get("role") == "assistant" and m.get("tool_calls"))
assert ctx.describe_history([]) == []
print(f"6i. describe_history：{len(shown)} 行，摘要行已标注；空历史返回空列表")

# Test invariant or expected behavior.
# Test invariant or expected behavior.
# Test invariant or expected behavior.
# Test invariant or expected behavior.
