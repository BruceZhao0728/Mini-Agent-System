"""Parallel scheduling: overlap, ordering, failure containment, disabled path."""
import threading
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace

from agent import ReActAgent
from agent import execution
from context import validate_history
from tools import TOOLS, TOOLS_DESC, TOOL_META

agent = ReActAgent()
_added = []


def add_tool(name, fn, safe):
    assert name not in TOOLS and name not in TOOL_META, name
    TOOLS[name] = fn
    TOOL_META[name] = {"parallel_safe": safe}
    _added.append(name)


def cleanup():
    for name in _added:
        TOOLS.pop(name, None)
        TOOL_META.pop(name, None)
    _added.clear()


def call(name, args, call_id):
    return SimpleNamespace(
        id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=args))


def run_calls(calls, parallel=None):
    with redirect_stdout(StringIO()):
        return agent._execute_tool_calls(calls, parallel)


old_enabled = execution.PARALLEL_ENABLED
old_workers = execution.PARALLEL_MAX_WORKERS
old_cap = execution.MAX_TOOL_CALLS_PER_ROUND
old_log = execution.EXPORT_LOG
execution.PARALLEL_ENABLED = True
execution.PARALLEL_MAX_WORKERS = 2
execution.EXPORT_LOG = False   # 不让测试写 Parallel/Tool duration/retry 日志

try:
    # 0) 真实工具的 safe 集合；过滤本测试的 t_ fakes 和可选的 probe_
    expected_safe = {
        "read_file", "list_directory", "search_web", "fetch_webpage",
        "get_news", "git_status", "git_diff", "git_log", "recall_notes",
    }
    actual_safe = {n for n, m in TOOL_META.items()
                   if m.get("parallel_safe") is True
                   and not n.startswith(("t_", "probe_"))}
    assert actual_safe == expected_safe, actual_safe
    assert all("parallel_safe" not in tc["function"] for tc in TOOLS_DESC)

    # (a) 两个 safe 调用真的重叠。串行实现会让 barrier 超时 -> Error。
    barrier = threading.Barrier(2, timeout=2)
    def overlap(n):
        barrier.wait()
        return f"ok-{n}"
    add_tool("t_overlap", overlap, True)
    out = run_calls([
        call("t_overlap", '{"n": 1}', "c1"),
        call("t_overlap", '{"n": 2}', "c2"),
    ])
    assert [m["tool_call_id"] for m in out] == ["c1", "c2"], out
    assert all(m["content"].startswith("ok-") for m in out), out

    # (b) unsafe 不重叠，且在主线程执行。
    unsafe_barrier = threading.Barrier(2, timeout=2)
    def never_two(n):
        try:
            unsafe_barrier.wait()
        except threading.BrokenBarrierError:
            return "serial"
        return "OVERLAP"
    add_tool("t_unsafe", never_two, False)
    out = run_calls([
        call("t_unsafe", '{"n": 1}', "u1"),
        call("t_unsafe", '{"n": 2}', "u2"),
    ])
    assert [m["content"] for m in out] == ["serial", "serial"], out

    main_ids = []
    def on_main(n):
        main_ids.append(threading.get_ident())
        return "u"
    add_tool("t_main", on_main, False)
    run_calls([call("t_main", '{"n": 1}', "m1"),
               call("t_main", '{"n": 2}', "m2")])
    assert main_ids == [threading.main_thread().ident] * 2, main_ids

    # (c) 连续段保持 read-after-write 顺序。
    store = {}
    def write_x(**_):
        store["x"] = "written"
        return "wrote"
    def read_x(**_):
        return store.get("x", "unset")
    add_tool("t_write", write_x, False)
    add_tool("t_read", read_x, True)
    out = run_calls([
        call("t_read", "{}", "r0"),
        call("t_write", "{}", "w1"),
        call("t_read", "{}", "r2"),
    ])
    assert [m["content"] for m in out] == ["unset", "wrote", "written"], out

    # (d) 完成顺序可以反转，history 顺序不能。
    release = threading.Event()
    def slow(**_):
        if not release.wait(2):
            # 非重试异常：失败时不要触发 _call_tool 的 0.5+1+2s 退避
            raise RuntimeError("second call never started")
        return "slow"
    def fast(**_):
        release.set()
        return "fast"
    add_tool("t_slow", slow, True)
    add_tool("t_fast", fast, True)
    out = run_calls([call("t_slow", "{}", "s1"),
                     call("t_fast", "{}", "s2")])
    assert [m["tool_call_id"] for m in out] == ["s1", "s2"], out
    assert [m["content"] for m in out] == ["slow", "fast"], out

    # (e) 部分失败 + BaseException 仍然每个 slot 都有结果。
    def boom(**_):
        raise RuntimeError("boom")
    def die(**_):
        raise SystemExit(7)
    def ok(**_):
        return "ok"
    for name, fn in (("t_boom", boom), ("t_die", die), ("t_ok", ok)):
        add_tool(name, fn, True)
    out = run_calls([
        call("t_boom", "{}", "f1"),
        call("t_ok", "{}", "f2"),
        call("t_die", "{}", "f3"),
    ])
    assert [m["tool_call_id"] for m in out] == ["f1", "f2", "f3"], out
    assert "RuntimeError" in out[0]["content"], out
    assert out[1]["content"] == "ok", out
    assert "SystemExit" in out[2]["content"], out

    # (f) cap / parse / unknown 仍各有一条消息。
    execution.MAX_TOOL_CALLS_PER_ROUND = 1
    out = run_calls([call("t_ok", "{}", "cap1"),
                     call("t_ok", "{}", "cap2")])
    execution.MAX_TOOL_CALLS_PER_ROUND = old_cap
    assert "maximum number of tool calls" in out[1]["content"], out
    out = run_calls([call("t_ok", "{bad", "p1"),
                     call("t_ok", "{}", "p2")])
    assert "Invalid JSON" in out[0]["content"], out
    out = run_calls([call("t_missing", "{}", "m1")])
    assert "Error" in out[0]["content"], out

    # (g) enabled/disabled 的 tool 消息逐项相等。
    calls = [call("t_ok", "{}", "e1"), call("t_ok", "{}", "e2")]
    enabled = run_calls(calls, True)
    disabled = run_calls(calls, False)
    assert enabled == disabled, (enabled, disabled)
    execution.PARALLEL_ENABLED = False
    assert run_calls(calls) == disabled
    execution.PARALLEL_ENABLED = True

    # (h) 返回的消息能通过 history 协议（必须用同一组 calls 的结果）。
    history = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": c.id, "type": "function",
             "function": {"name": c.function.name,
                          "arguments": c.function.arguments}}
            for c in calls]},
        *disabled,
    ]
    assert validate_history(history) == [], validate_history(history)
    print("Parallel: safe set / overlap / unsafe serial / order / failure / cap / disabled / history")
finally:
    cleanup()
    execution.PARALLEL_ENABLED = old_enabled
    execution.PARALLEL_MAX_WORKERS = old_workers
    execution.MAX_TOOL_CALLS_PER_ROUND = old_cap
    execution.EXPORT_LOG = old_log
