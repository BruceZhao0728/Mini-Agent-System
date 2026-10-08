import contextlib, io, os, shutil, subprocess, sys, tempfile, time
from types import SimpleNamespace

os.makedirs("logs", exist_ok=True)          # _call_tool 里的日志写入需要它
from react_agent import ReActAgent, _answer_warning, _message_dict
from tools import TOOLS
from config import MAX_TOOL_RETRIES
import context as ctx
import main as M
from utils import clip
import memory as mem

a = ReActAgent()

# ---- 1. 暂时性失败：前两次失败，第三次成功 --------------------------------
# 守住：重试真的会发生，且退避时长是 0.5 + 1.0
calls = {"n": 0}
def flaky():
    calls["n"] += 1
    if calls["n"] < 3:                       # 前两次失败，第三次成功
        raise subprocess.TimeoutExpired("cmd", 30)
    return "ok"
TOOLS["flaky"] = flaky

t0 = time.time()
out = a._call_tool("flaky", {})
dt = time.time() - t0
print(f"1. {out!r} calls={calls['n']} {dt:.2f}s")
assert out == "ok", out
assert calls["n"] == 3, calls["n"]
assert 1.4 <= dt <= 3.0, f"退避时长 {dt:.2f}s 不在 [1.4, 3.0]"

# ---- 2. 确定性失败：一次都不该重试 ----------------------------------------
# 守住：分类。如果有人把 OSError 放进 RETRIABLE_ERRORS，这里会变成 calls=4
calls2 = {"n": 0}
def deterministic():
    calls2["n"] += 1
    raise FileNotFoundError("nope.md")
TOOLS["deterministic"] = deterministic

t0 = time.time()
out = a._call_tool("deterministic", {})
dt = time.time() - t0
print(f"2. {out!r} calls={calls2['n']} {dt:.2f}s")
assert "FileNotFoundError" in out, out        # 类型名必须出现在交回模型的消息里
assert calls2["n"] == 1, f"确定性错误被重试了 {calls2['n']} 次"
assert dt < 0.5, dt

# ---- 3. 上界：永远失败，恰好重试 MAX_TOOL_RETRIES 次后放弃 -----------------
# 守住：不会无限重试；总退避 = 0.5 + 1 + 2 = 3.5s
calls3 = {"n": 0}
def always_timeout():
    calls3["n"] += 1
    raise subprocess.TimeoutExpired("cmd", 30)
TOOLS["always_timeout"] = always_timeout

t0 = time.time()
out = a._call_tool("always_timeout", {})
dt = time.time() - t0
print(f"3. {out!r} calls={calls3['n']} {dt:.2f}s")
assert calls3["n"] == MAX_TOOL_RETRIES + 1, calls3["n"]
assert "TimeoutExpired" in out, out
assert 3.2 <= dt <= 6.0, f"总退避 {dt:.2f}s 不在 [3.2, 6.0]"

# ---- 4. 参数解析：语法错误 vs 类型错误 -------------------------------------
# 守住：判据 ①。坏 JSON 只有这一条进入路径，所以测这个收口点就够了。
def tc(args):   # 造一个最小的 tool_call
    return SimpleNamespace(function=SimpleNamespace(name="read_file", arguments=args))

cases = [
    # arguments                    期望的 args                错误消息里必须出现的片段
    ('{path: "x"}',               {},                        "Invalid JSON"),
    ('',                          {},                        ""),              # 空串 = 无参数，合法
    ('[1,2]',                     {},                        "JSON object"),   # 合法 JSON，但不是对象
    ('3',                         {},                        "JSON object"),
    ('{"path": "/etc/hostname"}', {"path": "/etc/hostname"}, ""),
]
for raw, exp_args, exp_frag in cases:
    args, err = a._parse_args(tc(raw))
    ok = args == exp_args and exp_frag in err
    print(f"4. {raw!r:28} -> ({args}, {err[:48]!r})  {'ok' if ok else 'FAIL'}")
    assert ok, f"输入 {raw!r}：args={args} err={err!r}"

# ---- 5. 插件加载器：加载期校验与降级 ---------------------------------------
# 守住：作者错误在 **import 时** 就被抓住，而不是等模型第一次调用那个工具才炸
# NameError。tools/__init__.py 里那套校验以前一行测试都没有。
#
# 必须在子进程里测：这些错误都发生在 import 期，同进程里第一次 import 之后
# sys.modules 就缓存住了，注入故障再 import 也不会重新执行。
TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")


def _patch(pkg, rel, old, new):
    p = os.path.join(pkg, rel)
    s = open(p, encoding="utf-8").read()
    assert old in s, f"注入锚点已失效，这组测试需要跟着改: {rel} / {old[:60]!r}"
    open(p, "w", encoding="utf-8").write(s.replace(old, new, 1))


def _none(pkg): pass
def _dup_name(pkg):
    # 给 read_file 换个名字，撞上 list_directory
    _patch(pkg, "local.py", 'required=["path"],\n)\ndef read_file',
                          'required=["path"],\n    name="list_directory",\n)\ndef read_file')
def _bad_required(pkg):
    _patch(pkg, "local.py", 'required=["path"],', 'required=["path","nope"],')
def _undef_in_tool(pkg):
    # read_file / write_file / list_directory 都直接用 os
    _patch(pkg, "local.py", "import os\nimport subprocess\n", "import subprocess\n")
def _undef_in_helper(pkg):
    # subprocess 只有 helper _run_git 用 —— 这正是当初漏掉的那个洞：
    # 走工具函数扫不到它，只有扫整份模块源码才抓得住
    _patch(pkg, "vcs.py", "import os\nimport subprocess\n", "import os\n")
def _no_return(pkg):
    _patch(pkg, "_spec.py", "        return fn\n    return deco", "        pass\n    return deco")
def _broken_plugin(pkg):
    open(os.path.join(pkg, "vcs.py"), "w", encoding="utf-8").write("@tool(\n")


def load_with(inject):
    """把 tools/ 复制到临时目录，注入故障，在子进程里 import，返回 (rc, 输出)。"""
    with tempfile.TemporaryDirectory() as d:
        pkg = os.path.join(d, "tools")
        shutil.copytree(TOOLS_DIR, pkg, ignore=shutil.ignore_patterns("__pycache__"))
        inject(pkg)
        r = subprocess.run([sys.executable, "-c",
                            "import tools; print('LOADED', len(tools.TOOLS), tools.FAILED)"],
                           cwd=d, capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr


loader_cases = [
    # 说明                              注入              期望 rc   输出里必须出现
    # 期望片段是英文：⑥ 把 tools/ 的注释、docstring 连同这几条 RuntimeError 一起
    # 译成了英文，测试这边当时没跟上，于是这一组自 ⑥ 起一直是红的（本目录
    # 2026-10-08 修正）。加载器的英文是规范文本，测试是过时的一方。
    ("原样加载（不该报错）",             _none,            0,        "LOADED"),
    ("插件整个坏掉（降级，不该 raise）",  _broken_plugin,   0,        "'vcs'"),
    ("工具名重复（会静默覆盖）",         _dup_name,        1,        "duplicate tool name(s)"),
    ("required 不在 properties 里",      _bad_required,    1,        "absent from properties"),
    ("工具函数引用未定义的名字",         _undef_in_tool,   1,        "references undefined name(s)"),
    ("helper 引用未定义的名字",          _undef_in_helper, 1,        "references undefined name(s)"),
    ("deco 忘了 return fn",              _no_return,       1,        "forget to return fn"),
]
for desc, inject, exp_rc, frag in loader_cases:
    rc, out = load_with(inject)
    ok = rc == exp_rc and frag in out
    print(f"5. {desc:30} rc={rc} (期望 {exp_rc})  {'ok' if ok else 'FAIL'}")
    if not ok:
        print("   " + out.strip().replace("\n", "\n   ")[-400:])
    assert ok, f"{desc}: rc={rc} 期望 {exp_rc}；输出里没找到 {frag!r}"

# ---- 6. 上下文压缩：切点、校验、组装（全部离线，不调 API） -----------------
# 守住：压缩产出的历史永远合法，且永远比原来小。这类违规——tool_calls 与
# tool 结果对不上、assistant 丢了 reasoning_content——在真实运行里表现为
# **偶发 400**，跑几次根本测不出来，只能靠这里的确定性检查（COMPACT.md §2.2）。
def mk(turns, groups=2):
    """造一段形状真实的历史：每轮 = user + N 组(assistant+tool) + 收尾 assistant。"""
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


# 6a. truncate 真的截断 —— clip 不会（COMPACT.md §1.5 记录的那个陷阱）
assert len(ctx.truncate("x" * 3000, 1000)) < 1100
assert ctx.truncate("abc", 10) == "abc"
assert ctx.truncate("abc", 0) == ""       # COMPACT_REASONING_CHARS = 0 靠的就是这个语义
assert clip("x" * 3000, 1000, 500) == "x" * 3000, "clip 行为变了 —— COMPACT.md §1.5 要更新"
print("6a. truncate 生效；clip(x,1000,500) 对 3000 字符仍原样返回（已知行为，见 §1.5）")

# 6b. chunk_text 无损，且每段不超长（折叠总结靠它）
long_text = "\n".join(f"line {i} " + "x" * 30 for i in range(50))
assert "".join(ctx.chunk_text(long_text, 200)) == long_text
assert all(len(c) <= 200 for c in ctx.chunk_text(long_text, 200))
assert ctx.chunk_text("", 10) == [""]
assert ctx.chunk_text(long_text, 0) == [long_text]
print("6b. chunk_text 无损且不超长")

# 6c. split_history 边界表（每轮 6 条，轮起点在 1/7/13/…）
assert ctx.split_history([{"role": "system", "content": "S"}]) is None
assert ctx.split_history(mk(1)[:2]) is None
assert ctx.split_history(mk(1)) is None            # 1 轮 2 组：切了也没有整组可总结
assert ctx.split_history(mk(2)) == 7               # 保留最后一轮
assert ctx.split_history(mk(3)) == 7               # 保留最后两轮
assert ctx.split_history(mk(1, groups=10)) == 10   # 单轮很长 -> 退到按组切
for msgs in (mk(2), mk(3), mk(5), mk(1, groups=10)):
    cut = ctx.split_history(msgs)
    legal = {i for i, m in enumerate(msgs)
             if m.get("role") == "user"
             or (m.get("role") == "assistant" and m.get("tool_calls"))}
    assert cut in legal, f"切点 {cut} 既不是轮边界也不是组起点"
print("6c. split_history：6 个边界用例 + 切点只落在合法位置")

# 纯对话（完全不用工具）也必须能压缩——早期版本要求压缩区里含"工具调用组"，
# 结果多轮闲聊永远压不动。
chat_only = [{"role": "system", "content": "SYS"}]
for t in range(4):
    chat_only.append({"role": "user", "content": f"问题 {t}" + "u" * 300})
    chat_only.append({"role": "assistant", "content": f"回答 {t}" + "a" * 300,
                      "reasoning_content": ""})
assert ctx.split_history(chat_only) == 5, ctx.split_history(chat_only)
print("6c. 纯对话历史（无工具）也能切：cut =", ctx.split_history(chat_only))

# 6d. validate_history 必须拦下五种非法形态
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
# 反向：整个会话都没有 reasoning_content（thinking 关闭）时不能误报
no_thinking = [{k: v for k, v in m.items() if k != "reasoning_content"} for m in good]
assert ctx.validate_history(no_thinking) == [], ctx.validate_history(no_thinking)
print("6d. 反例：thinking 关闭的历史不误报")

# 6e. 属性测试：任意历史 x 任意 keep_turns，产物必须合法且严格变小
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

# 6f. 组装规则：摘要并入后一条 user 消息、不出现连续同角色、不污染调用方的历史
msgs = mk(3)
cut = ctx.split_history(msgs)
new = ctx.build_compacted(msgs, cut, "THE SUMMARY")
assert new[0]["role"] == "system"
assert new[1]["role"] == "user" and "THE SUMMARY" in new[1]["content"]
assert "question 1" in new[1]["content"]      # 原来的提问还在，没被摘要挤掉
for i in range(1, len(new)):
    prev_role, role = new[i - 1]["role"], new[i]["role"]
    assert not (prev_role == role and role in ("user", "assistant")), f"messages[{i}] 连续同角色"
before = msgs[cut]["content"]
ctx.build_compacted(msgs, cut, "SHOULD NOT LEAK")
assert msgs[cut]["content"] == before, "build_compacted 改动了调用方的历史"
print("6f. 组装：合并进 user、无连续同角色、不污染原历史")

# 6g. 折叠：上一次的摘要必须进入下一次总结的输入，否则会静默失忆
h = mk(6)
h = ctx.build_compacted(h, ctx.split_history(h), "SUMMARY-ONE")
c2 = ctx.split_history(h)
assert "SUMMARY-ONE" in ctx.render_transcript(h[1:c2], tool_chars=1200), \
    "上一次的摘要没进下一次总结的输入 —— 会失忆"
print("6g. 折叠：第二次压缩的输入里含第一次的摘要")

# 6h. compact() 的失败路径：先建后换，历史必须原封不动
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
# 没有可压缩的内容时必须明确作答，不能静默无操作
ag2 = ReActAgent()
ag2.messages = mk(1)
changed, msg = ag2.compact()
assert changed is False and "Nothing to compact" in msg, msg
print(f"6h. 无可压缩内容      -> {msg}")

# 6i. describe_history：一行一条消息，摘要那条要能一眼认出来
shown = ctx.describe_history(new)
assert len(shown) == len(new), (len(shown), len(new))
assert sum(1 for line in shown if "← summary" in line) == 1, shown
assert sum(1 for line in shown if "tool_calls" in line) == \
       sum(1 for m in new if m.get("role") == "assistant" and m.get("tool_calls"))
assert ctx.describe_history([]) == []
print(f"6i. describe_history：{len(shown)} 行，摘要行已标注；空历史返回空列表")

# ---- 7. 终端渲染：表格、LaTeX、流式等价（全部离线） ------------------------
# 守住两件事：表格按【显示宽度】对齐（CJK 双宽），以及——最关键的一条——
# 流式渲染和整体渲染必须逐字节一致。后者一旦破掉，症状是"边流边显示的内容"
# 和日志里存的内容不一样，而且只在某些分块边界下出现。
import utils as U

# 7a. 显示宽度：ANSI 不占列，CJK 占两列
assert U.display_width("\x1b[1mab\x1b[22m") == 2
assert U.display_width("中文") == 4 and U.display_width("abc") == 3
print("7a. display_width：ANSI 记 0、CJK 记 2")

# 7b. LaTeX → Unicode
assert U.latex_to_text(r"E = mc^2") == "E = mc²", U.latex_to_text(r"E = mc^2")
assert U.latex_to_text(r"\alpha \leq \beta \times \gamma") == "α ≤ β × γ"
assert U.latex_to_text(r"\frac{-b \pm \sqrt{b^2-4ac}}{2a}") == "(-b ± √(b²-4ac))/(2a)"
assert U.latex_to_text(r"\sum_{i=1}^{n} x_i^2") == "∑ᵢ₌₁ⁿ xᵢ²"
assert U.latex_to_text(r"\mathbb{R}^n \to \mathbb{R}") == "ℝⁿ → ℝ"
# 数论/代数里最常写的那几个，缺了它们整段公式就是半截 LaTeX
assert U.latex_to_text(r"b \mid a \iff \exists c") == "b ∣ a ⟺ ∃ c"
assert U.latex_to_text(r"d \mid a \implies d \mid (ua+vb)") == "d ∣ a ⟹ d ∣ (ua+vb)"
assert U.latex_to_text(r"a \equiv b \pmod{m}") == "a ≡ b (mod m)"
assert U.latex_to_text(r"p \nmid a") == "p ∤ a"
assert U.latex_to_text(r"\gcd(a,b) \cdot \lcm(a,b)") == "gcd(a,b) · lcm(a,b)"
print("7b. latex_to_text：希腊字母 / 运算符 / 上下标 / 嵌套分式根式 / 数论符号")

# 7c. 货币不能被当成公式
for s in ("价格是 $100 and $200 美元", "成本 $5。", "总共 $1,234.56"):
    assert U.markdown_to_ansi(s) == s, s
assert "\x1b[35m" in U.markdown_to_ansi("变量 $x$ 和 $x^2$")
print("7c. $…$ 只在像公式时渲染；货币原样保留")

# 7d. 表格按显示宽度对齐 —— 这是加表格渲染的全部理由
table = ("| 常量名 | 值 | 中文说明 |\n| --- | --- | --- |\n"
         "| MAX_ROUNDS | 30 | 主循环允许的最大轮数 |\n"
         "| MAX_OBS_CHARS | 8000 | 每轮写入历史的工具输出字符数上限 |")
rendered = U.markdown_to_ansi(table)
widths = {U.display_width(line) for line in rendered.split("\n")}
assert len(widths) == 1, f"表格各行显示宽度不一致：{sorted(widths)}"
assert "│" in rendered and "|" not in rendered.replace("│", "")
print(f"7d. 表格：{len(rendered.split(chr(10)))} 行，显示宽度全部 = {widths.pop()}")

# 7d-2. 单元格里的竖线不能切开单元格 —— 这是实际踩到的那个 bug：
#       模型写 `p^k | n` 或 $d \mid a$（不转义），按所有 | 切会把 4 列撑成 8 列、
#       每句话都被从中间截断。GFM 要求写成 \|，模型不照做。
pipe_table = ("| # | 概念 | 要点 | 章节 |\n|---|---|---|---|\n"
              "| 9 | p 进指数 | 使 `p^k | n` 的最大 k | 1.5 |\n"
              "| 1 | 整除 | $d \\mid a$ 与 $d \\mid b$ | 1.1 |")
rendered = U.markdown_to_ansi(pipe_table)
assert rendered.split("\n")[0].count("│") - 1 == 4, "单元格里的竖线把表格切碎了"
for frag in ("p^k | n", "的最大 k", "1.5", "1.1", "与"):
    assert frag in U.strip_ansi(rendered), frag
assert "\\|" not in rendered            # 转义的 \| 还原成字面竖线
print("7d-2. 单元格内的 | 不再切碎表格：4 列、语句完整")

# 7e. 表格不截断内容、列宽随终端收缩（用换行换完整）
narrow = U._render_table(pipe_table.split("\n"), budget=40)
assert max(U.display_width(l) for l in narrow.split("\n")) <= 40
assert "…" not in narrow and "1.5" in narrow and "1.1" in narrow
assert len(narrow.split("\n")) > len(U.markdown_to_ansi(pipe_table).split("\n"))
print("7d-3. 窄终端下换行而非截断：宽度 <= 40，内容一条不少")

# 7e. 代码围栏里的表格不动
assert "│" not in U.markdown_to_ansi("```\n| a | b |\n| --- | --- |\n```")
print("7e. 代码围栏里的表格不渲染")

# 7f. 行内格式与链接同行 —— 这是一处修复过的老 bug：
#     链接规则 `\[…\]\(…\)` 会匹配到 ANSI 转义里的 '['（\x1b[1m），
#     所以现在先把链接摘出来，再跑强调规则。
got = U.markdown_to_ansi("`code` **b** *i* [l](u)")
expect = ("\x1b[36mcode\x1b[39m \x1b[1mb\x1b[22m \x1b[3mi\x1b[23m "
          "\x1b[4ml\x1b[24m\x1b[2m (u)\x1b[22m")
assert got == expect, repr(got)
print("7f. 粗体 + 链接同行：链接规则不再匹配到 ANSI 里的 '['")

# 7g. 流式等价：任意分块粒度下 feed()+flush() 必须等于整体渲染
stream_docs = [table + "\n", table, "```\n| a | b |\n| - | - |\n```\n",
               "文字 $x^2$ 与 **粗体**\n\n| a |\n|---|\n| 1 |", "", "\n\n"]
stream_checks = 0
for doc in stream_docs:
    whole = U.markdown_to_ansi(doc)
    for step in (1, 2, 3, 7, 40, 1000):
        stream = U.MarkdownStream()
        got = "".join(stream.feed(doc[i:i + step]) for i in range(0, len(doc), step))
        got += stream.flush()
        # 流式没有"文末空行"这个概念（_consume_stream 最后自己 print() 收尾），
        # 所以整体渲染最多允许比它多一个尾换行。
        assert whole == got or whole == got + "\n", \
            (doc[:24], step, repr(whole[-40:]), repr(got[-40:]))
        stream_checks += 1
print(f"7g. 流式等价：{len(stream_docs)} 篇文档 x 6 种分块粒度 = {stream_checks} 次比对全部一致")

# ---- 8. 收尾回复的完整性：截断/空回答不能被当成成功（全部离线） --------------
# 守住：2026-10-08 真实发生过一次——reasoning 4,791 字符之后，回答只写了 72 个
# 字符就中断；循环打的是 [Done]、把残句写进历史、当正常回答返回。当时诊断不了
# 的原因是 finish_reason 压根没被记录。这一组盖住两半：判据函数，与
# _consume_stream 是否真把 finish_reason 收了下来。

# 8a. 判据表：只有真出问题才出声（工具轮不在此列——那里的裸 "Thought:" 是常态）
answer_cases = [
    # 说明                         content                                            finish    期望警告
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

# 8a-2. 两条同时成立时，报更具体的那条：被截断 > 没写
assert "output limit" in _answer_warning("", "length")
print("8a-2. length 与空回答同时成立 -> 报被截断（更具体的那条）")

# 8b. _consume_stream 收下 finish_reason，且 usage 的两种落点都还在
def fake_chunk(content=None, reasoning=None, finish=None, usage=None, with_choices=True):
    """造一个最小可用的流块，形状照抄 SDK 的 chunk.choices[0].delta。"""
    delta = SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=None)
    choices = [SimpleNamespace(delta=delta, finish_reason=finish)] if with_choices else []
    return SimpleNamespace(choices=choices, usage=usage)

usage_obj = SimpleNamespace(prompt_tokens=10, completion_tokens=3, total_tokens=13)
# DeepSeek 的落点：usage 骑在最后一个内容块上，同块 finish_reason 已置位
stream_a = [fake_chunk(reasoning="让我想想"),
            fake_chunk(content="Thought: 好"),
            fake_chunk(content="了", finish="stop", usage=usage_obj)]
# OpenAI 兼容网关的落点：另发一个 choices=[] 的纯 usage 尾块
stream_b = [fake_chunk(content="Final Answer: ok", finish="length"),
            fake_chunk(usage=usage_obj, with_choices=False)]

with contextlib.redirect_stdout(io.StringIO()):     # _consume_stream 会边收边打印
    m_a = a._consume_stream(iter(stream_a))
    m_b = a._consume_stream(iter(stream_b))

assert m_a.content == "Thought: 好了", repr(m_a.content)
assert m_a.reasoning_content == "让我想想" and m_a.tool_calls is None
assert m_a.finish_reason == "stop", m_a.finish_reason
assert m_a.usage.total_tokens == 13
assert m_b.finish_reason == "length" and m_b.usage.total_tokens == 13
assert _message_dict(m_b)["finish_reason"] == "length"       # 日志里必须查得到
print("8b. _consume_stream：finish_reason 进 msg 也进日志；usage 两种落点都还在")

# 8c. 端到端：run() 遇到空回答必须出声，而不是安静地返回空串
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

# ---- 9. 长期记忆：写入、检索、索引（全部离线） ------------------------------
# 守住：MEMORY.md §7 判据里能在无网络下验证的部分。跨进程那两条要真调 API，
# 不在这里。第 9 组必须新建 agent —— 8c 把共享的 a 的 client 换成了 lambda。

mem_dir = tempfile.mkdtemp()
mem_path = os.path.join(mem_dir, "notes.jsonl")

# 9a. 中文分词：空格切分对中文是坏的，二元组才对
assert "作者" in mem._terms("作者用中文提问"), "中文没有二元组 —— 检索会整体失效"
assert "中文" in mem._terms("作者用中文提问")
assert mem._terms("   ") == set()
assert mem._terms("a b cd") == {"cd"}, mem._terms("a b cd")
print("9a. 中文二元组 + 拉丁词；单字符词被丢掉（空格分词对中文无效）")

# 9b. 空库
assert mem.load(mem_path) == []
assert mem.render_index(mem_path) == "", "空库不该产生索引块"
assert "empty" in mem.render_recall(*mem.search("任意", path=mem_path), query="任意")
print("9b. 空库：load=[]，索引为空串，召回明确说'空'而不是返回空串")

# 9c. 写入 + id 自增
ok, _ = mem.append("第一条", "正文一", "user", path=mem_path)
assert ok and mem.load(mem_path)[0]["id"] == 1
ok, _ = mem.append("第二条", "", "project", path=mem_path)
assert ok and [n["id"] for n in mem.load(mem_path)] == [1, 2]
print("9c. 写入两条，id 依次为 1 / 2")

# 9d. 幂等：同样的 summary 不重复写
before = len(mem.load(mem_path))
ok, _ = mem.append("第一条", "不一样的正文", "user", path=mem_path)
assert ok is False and len(mem.load(mem_path)) == before
print("9d. 重复 summary 不写入（写入路径幂等）")

# 9e. 检索排序：全串命中 1.0 且排第一
mem.append("conda 解算器会重写整个环境", "别用它修包", "project", path=mem_path)
matched, rows = mem.search("第一条", path=mem_path)
assert matched and rows[0][1]["summary"] == "第一条" and rows[0][0] == 1.0
print("9e. 全串命中得 1.0 且排第一")

# 9f. 无命中：回退最近 k 条，且 matched=False（不能假装命中）
matched, rows = mem.search("量子色动力学", k=2, path=mem_path)
assert matched is False and len(rows) == 2
assert mem.render_recall(matched, rows, "量子色动力学").startswith("No note matched")
print("9f. 无命中时回退最近 k 条并标注 matched=False")

# 9g. 索引上限：按条数、按字符数都生效，截断提示一定出现
idx = mem.render_index(mem_path, max_notes=10, max_chars=10000)
assert idx.count("\n") == 2 and "not shown" not in idx
idx = mem.render_index(mem_path, max_notes=1, max_chars=10000)
assert idx.count("\n") == 1 and "(2 more not shown" in idx, idx
assert "conda" in idx, "上限应该保留最新的，挤掉最旧的"
idx = mem.render_index(mem_path, max_notes=10, max_chars=10)
assert "not shown" in idx and len(idx) < 200 and not idx.startswith("\n"), idx
print("9g. 索引上限：按条数、按字符数都生效，截断提示一定出现")

# 9h. 两处上限常量不能漂移（memory.py 的默认值 vs config.py 的真值）
from config import MEMORY_INDEX_MAX_NOTES, MEMORY_INDEX_MAX_CHARS
assert mem.INDEX_MAX_NOTES == MEMORY_INDEX_MAX_NOTES
assert mem.INDEX_MAX_CHARS == MEMORY_INDEX_MAX_CHARS
print("9h. memory.py 的默认上限与 config.py 的真值一致")

# 9i. reset() 把索引拼进 messages[0]，不是新加一条消息
real_path = mem.NOTES_PATH
mem.NOTES_PATH = mem_path
try:
    ag9 = ReActAgent()
    ag9.reset()
finally:
    mem.NOTES_PATH = real_path
assert ag9.memory_index and "conda" in ag9.memory_index
assert len(ag9.messages) == 1 and ag9.messages[0]["role"] == "system"
assert ag9.memory_index in ag9.messages[0]["content"]
assert ag9.memory_index not in ag9.system_prompt, "索引不该污染基础 prompt"
print("9i. reset()：索引拼进 messages[0]，历史仍只有 1 条，基础 prompt 未被污染")

# 9j. 删除：按 id 删一条，其余不动，id 不复用
#     此时库里是 1 第一条 / 2 第二条 / 3 conda
idx = mem.render_index(mem_path, max_notes=10, max_chars=10000)
assert "[1]" in idx and "第一条" in idx, idx        # 索引必须带 id，否则 forget 要先 recall 一轮
before = [n["id"] for n in mem.load(mem_path)]
ok, msg = mem.forget(2, path=mem_path)
assert ok is True and mem.forget(2, path=mem_path)[0] is False, "第二次删同一条居然还成功"
assert [n["id"] for n in mem.load(mem_path)] == [1, 3], mem.load(mem_path)
assert len(mem.render_index(mem_path, 10, 10000).splitlines()) == 2, "删完索引没跟着变"
# 删不存在的 id：明确失败，且文件一个字节都不动
snapshot = open(mem_path, encoding="utf-8").read()
ok, msg = mem.forget(99, path=mem_path)
assert ok is False and "99" in msg, msg
assert open(mem_path, encoding="utf-8").read() == snapshot, "删不存在的 id 却改了文件"
# id 不复用：补一条新的，拿的是 4，不是被腾出来的 2
mem.append("第四条", "", "project", path=mem_path)
assert [n["id"] for n in mem.load(mem_path)] == [1, 3, 4], mem.load(mem_path)
print("9j. 删除：索引带 id；删一条其余不动；删不存在的 id 明确失败；id 不复用")

# 9j-2. 但删到【空库】之后 id 会从 1 重来 —— 有意为之，不是漏网
#       写这条是因为 forget() 的 docstring 一开始声称「id 永不复用」，实测不成立，
#       于是改的是说法而不是行为（.seq 计数器文件要额外同步，不值得）。钉住它。
for n in mem.load(mem_path):
    mem.forget(n["id"], path=mem_path)
assert mem.load(mem_path) == []
assert mem.render_index(mem_path) == "" and mem.forget(1, path=mem_path)[0] is False
mem.append("清空后重来", "", "project", path=mem_path)
assert mem.load(mem_path)[0]["id"] == 1, mem.load(mem_path)
print("9j-2. 删到空库后 id 从 1 重来（已固定，见 forget() 的 docstring）")

shutil.rmtree(mem_dir, ignore_errors=True)

# ---- 10. 命令行参数：--root / --msg / 位置参数（全部离线） -------------------
# 守住 2026-10-08 的接口改动。旧版靠「第一个参数是不是一个存在的目录」猜根目录，
# 于是 `main.py story` 会 chdir 进 story/ —— 而这个仓库里真有一个 story/ 目录。
# 现在根目录有自己的 flag，裸参数就能安全地当 prompt 了。
# parse_args 是纯函数：不碰文件系统、不读 env、不建目录，所以可以这样直接断言。

arg_cases = [
    # 说明                         argv                                 期望 (root, prompt)
    ("无参数 -> REPL",             [],                                  (None, None)),
    ("--root 与值分开",            ["--root", "/tmp"],                  ("/tmp", None)),
    ("--root=值",                  ["--root=/tmp"],                     ("/tmp", None)),
    ("--msg 与值分开",             ["--msg", "hi"],                     (None, "hi")),
    ("--msg=值",                   ["--msg=hi"],                        (None, "hi")),
    ("两个都给",                   ["--root", "/tmp", "--msg", "hi"],   ("/tmp", "hi")),
    ("裸 prompt（旧写法）",         ["a prompt"],                        (None, "a prompt")),
    ("裸 prompt 多词被拼回",        ["do", "several", "words"],          (None, "do several words")),
    ("story 不再被当成根目录",      ["story"],                           (None, "story")),
    ("--root + 裸 prompt",         ["--root", "/tmp", "hi"],            ("/tmp", "hi")),
    ("单个 - 当普通参数",           ["-"],                               (None, "-")),
]
for desc, argv, want in arg_cases:
    got = M.parse_args(argv)
    ok = got == want
    print(f"10. {desc:24} {str(argv):34} -> {got}  {'ok' if ok else 'FAIL'}")
    assert ok, f"{desc}: 期望 {want}，实得 {got}"

# 10b. --help 抛专用异常，且它在任何位置都优先
for argv in (["--help"], ["-h"], ["--root", "/tmp", "--help"]):
    try:
        M.parse_args(argv)
    except M._HelpRequested:
        continue
    raise AssertionError(f"{argv}: 应该抛 _HelpRequested")
print("10b. --help / -h 抛 _HelpRequested，位置任意（含 --root 之后）")

# 10c. 参数错误必须是明确的 ValueError，不能静默当成 prompt
for argv in (["--root"], ["--msg"], ["--root="], ["--msg="], ["--nope"],
             ["--msg", "x", "bare"]):
    try:
        M.parse_args(argv)
    except ValueError:
        continue
    raise AssertionError(f"{argv}: 应该抛 ValueError")
print("10c. 缺值 / 空值 / 未知选项 / prompt 给两次 -> 明确报错")

# 10d. parse_args 不碰文件系统：不存在的目录照样解析得出来（存在性由 __main__ 的
#      os.chdir 检查），这样它可以被无副作用地测试
assert M.parse_args(["--root", "/definitely/not/here"]) == ("/definitely/not/here", None)
print("10d. parse_args 纯函数：不检查目录是否存在、不建目录")


print(f"\n全部通过：重试 3 组 + 参数解析 {len(cases)} 例 + 插件加载 {len(loader_cases)} 例"
      f" + 上下文压缩 9 组（含 {checked} 个属性组合）+ 终端渲染 7 组"
      f"（含 {stream_checks} 次流式比对）+ 收尾回复判定 {len(answer_cases)} 例"
      f" + 长期记忆 10 组 + 命令行参数 {len(arg_cases)} 例")
