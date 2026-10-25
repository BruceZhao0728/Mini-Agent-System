"""Command-line parsing assertions."""

import main as M

arg_cases = [
    # Test invariant or expected behavior.
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

# Test invariant or expected behavior.
for argv in (["--help"], ["-h"], ["--root", "/tmp", "--help"]):
    try:
        M.parse_args(argv)
    except M._HelpRequested:
        continue
    raise AssertionError(f"{argv}: 应该抛 _HelpRequested")
print("10b. --help / -h 抛 _HelpRequested，位置任意（含 --root 之后）")

# Test invariant or expected behavior.
for argv in (["--root"], ["--msg"], ["--root="], ["--msg="], ["--nope"],
             ["--msg", "x", "bare"]):
    try:
        M.parse_args(argv)
    except ValueError:
        continue
    raise AssertionError(f"{argv}: 应该抛 ValueError")
print("10c. 缺值 / 空值 / 未知选项 / prompt 给两次 -> 明确报错")

# Test invariant or expected behavior.
# Test invariant or expected behavior.
assert M.parse_args(["--root", "/definitely/not/here"]) == ("/definitely/not/here", None)
print("10d. parse_args 纯函数：不检查目录是否存在、不建目录")


# Test invariant or expected behavior.
