"""Terminal Markdown and math rendering assertions."""

import io
from collections import namedtuple

import presentation as U
from presentation import plan_progress
from presentation.tables import _render_table

# Test invariant or expected behavior.
assert U.display_width("\x1b[1mab\x1b[22m") == 2
assert U.display_width("中文") == 4 and U.display_width("abc") == 3
print("7a. display_width：ANSI 记 0、CJK 记 2")

# Plan progress is a fixed-height, width-aware footer for interactive terminals.
progress_plan = [
    {"text": "读取项目文件", "status": "completed"},
    {"text": "summarize findings", "status": "in_progress"},
    {"text": "write report", "status": "pending"},
]
progress_lines = U.render_plan_progress(progress_plan, 22)
assert "1/3 completed" in progress_lines[0]
assert "✓ 1. 读取项目文件" in progress_lines[1]
assert "● 2. summarize findin…" in progress_lines[2]
assert "○ 3. write report" in progress_lines[3]
assert all(U.display_width(line) <= 22 for line in progress_lines)

class FakeTTY(io.StringIO):
    """String buffer that reports terminal capabilities for renderer assertions."""
    def isatty(self):
        return True

terminal_size = namedtuple("terminal_size", "columns lines")
old_get_terminal_size = plan_progress.shutil.get_terminal_size
plan_progress.shutil.get_terminal_size = lambda fallback: terminal_size(30, 12)
try:
    tty_output = FakeTTY()
    display = U.PlanProgressDisplay(stream=tty_output)
    display.update(progress_plan)
    display.close()
finally:
    plan_progress.shutil.get_terminal_size = old_get_terminal_size
tty_controls = tty_output.getvalue()
assert "\x1b[1;8r" in tty_controls       # 4 footer rows leave 8 output rows
assert "✓ 1. 读取项目文件" in tty_controls
assert "● 2. summarize findings" in tty_controls
assert "\x1b[r" in tty_controls          # restore full-screen scrolling
assert tty_controls.rstrip().endswith("○ 3. write report")

pipe_output = io.StringIO()
pipe_display = U.PlanProgressDisplay(stream=pipe_output)
pipe_display.update(progress_plan)
pipe_display.close()
assert pipe_output.getvalue() == ""
print("7a-2. 计划底栏：步骤状态、CJK宽度适配、TTY刷新与恢复、非TTY静默")

# 7b. LaTeX → Unicode
assert U.latex_to_text(r"E = mc^2") == "E = mc²", U.latex_to_text(r"E = mc^2")
assert U.latex_to_text(r"\alpha \leq \beta \times \gamma") == "α ≤ β × γ"
assert U.latex_to_text(r"\frac{-b \pm \sqrt{b^2-4ac}}{2a}") == "(-b ± √(b²-4ac))/(2a)"
assert U.latex_to_text(r"\sum_{i=1}^{n} x_i^2") == "∑ᵢ₌₁ⁿ xᵢ²"
assert U.latex_to_text(r"\mathbb{R}^n \to \mathbb{R}") == "ℝⁿ → ℝ"
# Test invariant or expected behavior.
assert U.latex_to_text(r"b \mid a \iff \exists c") == "b ∣ a ⟺ ∃ c"
assert U.latex_to_text(r"d \mid a \implies d \mid (ua+vb)") == "d ∣ a ⟹ d ∣ (ua+vb)"
assert U.latex_to_text(r"a \equiv b \pmod{m}") == "a ≡ b (mod m)"
assert U.latex_to_text(r"p \nmid a") == "p ∤ a"
assert U.latex_to_text(r"\gcd(a,b) \cdot \lcm(a,b)") == "gcd(a,b) · lcm(a,b)"
print("7b. latex_to_text：希腊字母 / 运算符 / 上下标 / 嵌套分式根式 / 数论符号")

# Test invariant or expected behavior.
for s in ("价格是 $100 and $200 美元", "成本 $5。", "总共 $1,234.56"):
    assert U.markdown_to_ansi(s) == s, s
assert "\x1b[35m" in U.markdown_to_ansi("变量 $x$ 和 $x^2$")
print("7c. $…$ 只在像公式时渲染；货币原样保留")

# Test invariant or expected behavior.
table = ("| 常量名 | 值 | 中文说明 |\n| --- | --- | --- |\n"
         "| MAX_ROUNDS | 30 | 主循环允许的最大轮数 |\n"
         "| MAX_OBS_CHARS | 8000 | 每轮写入历史的工具输出字符数上限 |")
rendered = U.markdown_to_ansi(table)
widths = {U.display_width(line) for line in rendered.split("\n")}
assert len(widths) == 1, f"表格各行显示宽度不一致：{sorted(widths)}"
assert "│" in rendered and "|" not in rendered.replace("│", "")
print(f"7d. 表格：{len(rendered.split(chr(10)))} 行，显示宽度全部 = {widths.pop()}")

# Test invariant or expected behavior.
# Test invariant or expected behavior.
# Test invariant or expected behavior.
pipe_table = ("| # | 概念 | 要点 | 章节 |\n|---|---|---|---|\n"
              "| 9 | p 进指数 | 使 `p^k | n` 的最大 k | 1.5 |\n"
              "| 1 | 整除 | $d \\mid a$ 与 $d \\mid b$ | 1.1 |")
rendered = U.markdown_to_ansi(pipe_table)
assert rendered.split("\n")[0].count("│") - 1 == 4, "单元格里的竖线把表格切碎了"
for frag in ("p^k | n", "的最大 k", "1.5", "1.1", "与"):
    assert frag in U.strip_ansi(rendered), frag
assert "\\|" not in rendered            # Test invariant or expected behavior.
print("7d-2. 单元格内的 | 不再切碎表格：4 列、语句完整")

# Test invariant or expected behavior.
narrow = _render_table(pipe_table.split("\n"), budget=40)
assert max(U.display_width(l) for l in narrow.split("\n")) <= 40
assert "…" not in narrow and "1.5" in narrow and "1.1" in narrow
assert len(narrow.split("\n")) > len(U.markdown_to_ansi(pipe_table).split("\n"))
print("7d-3. 窄终端下换行而非截断：宽度 <= 40，内容一条不少")

# Test invariant or expected behavior.
assert "│" not in U.markdown_to_ansi("```\n| a | b |\n| --- | --- |\n```")
print("7e. 代码围栏里的表格不渲染")

# Test invariant or expected behavior.
# Test invariant or expected behavior.
# Test invariant or expected behavior.
got = U.markdown_to_ansi("`code` **b** *i* [l](u)")
expect = ("\x1b[36mcode\x1b[39m \x1b[1mb\x1b[22m \x1b[3mi\x1b[23m "
          "\x1b[4ml\x1b[24m\x1b[2m (u)\x1b[22m")
assert got == expect, repr(got)
print("7f. 粗体 + 链接同行：链接规则不再匹配到 ANSI 里的 '['")

# Test invariant or expected behavior.
stream_docs = [table + "\n", table, "```\n| a | b |\n| - | - |\n```\n",
               "文字 $x^2$ 与 **粗体**\n\n| a |\n|---|\n| 1 |", "", "\n\n"]
stream_checks = 0
for doc in stream_docs:
    whole = U.markdown_to_ansi(doc)
    for step in (1, 2, 3, 7, 40, 1000):
        stream = U.MarkdownStream()
        got = "".join(stream.feed(doc[i:i + step]) for i in range(0, len(doc), step))
        got += stream.flush()
        # Test invariant or expected behavior.
        # Test invariant or expected behavior.
        assert whole == got or whole == got + "\n", \
            (doc[:24], step, repr(whole[-40:]), repr(got[-40:]))
        stream_checks += 1
print(f"7g. 流式等价：{len(stream_docs)} 篇文档 x 6 种分块粒度 = {stream_checks} 次比对全部一致")

# Test invariant or expected behavior.
# Test invariant or expected behavior.
# Test invariant or expected behavior.
# Test invariant or expected behavior.
# Test invariant or expected behavior.

# Test invariant or expected behavior.
