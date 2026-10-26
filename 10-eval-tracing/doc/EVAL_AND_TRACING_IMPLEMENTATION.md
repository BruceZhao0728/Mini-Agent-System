# Eval & Tracing：实现记录与逐文件改动

> 状态说明：本文写于 2026-10-26，是 `EVAL_AND_TRACING_DESIGN.md` 的配套实现记录。代码已在 2026-10-26 应用到本目录，并通过 `_test.py`、`eval/runner.py --suite smoke`、`eval/runner.py --suite failure`、`eval/trace.py --check` 的离线验证。文中代码直接取自应用后的源码；如果后续再改，以源码为准。
>
> 与设计文档的分工：设计文档回答“为什么 / 是什么 / 业界怎么做”；本文只回答“改了哪些文件、每段代码是什么、怎么验证”。行号是写作时的快照，阅读时以函数名和文件名为主。

---

## 0. 怎么用本文

### 0.1 两个文档的分工

| 文档 | 回答的问题 | 适合谁 |
| --- | --- | --- |
| `EVAL_AND_TRACING_DESIGN.md` | 为什么要 eval / trace；Claude Code、Codex CLI 怎么做；目标架构与判据 | 想理解设计、评审方案的人 |
| 本文 | 具体改了哪些文件；每段代码是什么；怎么跑测试 | 要复现、维护、继续开发的人 |

### 0.2 先备份，再改

本仓库没有 git，改坏无法回滚。应用本文代码前至少备份：

```bash
cd /home/bruce/code/mini-agent-system/10-eval-tracing
cp -a config.py main.py _test.py agent tests /tmp/eval_backup_$(date +%Y%m%d_%H%M%S)/
```

本次实际应用前的备份在 `/tmp/eval_impl_backup_20261026_162306`（只包含被修改的 `.py` 文件；这是临时路径，可能已被清理，不要把它当成长期快照）。

### 0.3 缩进规则

| 位置 | 缩进 |
| --- | --- |
| `main.py`、`config.py`、`agent/` | **tab** |
| `tracing.py`、`memory.py`、`eval/`、`tests/`、`_test.py` | 4 个空格 |
| `presentation/` 的代码模块 | tab（`__init__.py` 是空格） |

没有 linter；混用缩进不会被自动发现。

### 0.4 变更地图

| 文件 | 类型 | 作用 |
| --- | --- | --- |
| `tracing.py` | 新增 | JSONL trace writer，只依赖标准库 |
| `config.py` | 修改 | `TRACE_*` 开关、路径、轮转、脱敏；预算的 env override |
| `agent/core.py` | 修改 | 构造 `self.tracer`；保存 span / round 状态；可选 `client=` / `tracer=` |
| `agent/orchestration.py` | 修改 | `turn`、`plan_step`、`_end_turn` |
| `agent/planning.py` | 修改 | planner `model_request`、schema `verification`、`error` |
| `agent/react_loop.py` | 修改 | `round` start/end、answer `verification`、`MaxRounds` error |
| `agent/request.py` | 修改 | executor `model_request`、usage / finish、三个 error 分支 |
| `agent/execution.py` | 修改 | `tool_call`（phase 1）、`tool_result`（phase 3） |
| `agent/compaction.py` | 修改 | summarizer `model_request`、compaction `verification` |
| `main.py` | 修改 | trace 环境变量说明、启动时打印 trace 路径 |
| `eval/__init__.py` | 新增 | 包声明，不 import 重模块 |
| `eval/fake_model.py` | 新增 | 脚本化 `FakeClient`、`fake_chunk`、`tool_call_chunk` |
| `eval/assertions.py` | 新增 | 结构 / 轨迹 / 请求契约 / 结果 / 成本 / trace 断言 |
| `eval/runner.py` | 新增 | case 发现、隔离、表格、退出码、`--record` / `--replay` |
| `eval/recordings.py` | 新增 | 响应录制 / 回放、请求 hash、过期失败 |
| `eval/trace.py` | 新增 | trace 读取、校验、汇总（不是 writer） |
| `eval/cases/*.json` | 新增 | 10 个离线场景（6 smoke + 4 failure） |
| `tests/test_tracing.py` | 新增 | tracer 单测（组 11） |
| `tests/test_eval_harness.py` | 新增 | eval harness 单测（组 12） |
| `_test.py` | 修改 | 追加两个测试模块的 import |
| `README.md` | 修改 | 功能列表、布局、eval/trace 命令、差异与限制 |
| `CLAUDE.md` | 修改 | 状态、命令、测试组、日志 gate、本课特性说明 |

### 0.5 建议的应用顺序

| 阶段 | 内容 | 验证 |
| --- | --- | --- |
| 0 | `tracing.py` + `config.py` 的 `TRACE_*` | `python3 -m compileall -q .`；`MINI_AGENT_TRACE=0/1` 打印 |
| 1 | `agent/core.py` + 事件接入 | `python3 _test.py` 仍全绿 |
| 2 | `eval/fake_model.py` + `eval/runner.py` + 3 个 case | `python3 eval/runner.py --suite smoke` |
| 3 | `eval/assertions.py` + failure case | `python3 eval/runner.py --suite failure` |
| 4 | `eval/recordings.py` + `--record` / `--replay` | 离线 hash 测试 |
| 5 | `tests/*`、`_test.py`、README、CLAUDE | 全套验证 |

---

## 1. 实现后的行为

### 1.1 一次请求的 trace 树

```text
turn
├── model_request (planner)
├── plan_step 1
│   └── round 1
│       ├── model_request (executor)
│       └── tool_call / tool_result
├── plan_step 2
│   └── round 2
│       └── ...
└── turn end
```

直接 ReAct（没有计划）时，`round` 直接挂在 `turn` 下，`plan_step` 为 null。

### 1.2 事件与字段

每行一个 JSON 对象，公共字段：

```text
v, ts, trace_id, span_id, parent_span_id, event, actor,
turn, plan_step, round, round_global
```

事件类型：

| event | 关键字段 | 发射位置 |
| --- | --- | --- |
| `turn` | `status=start/end`、`prompt_chars`、`prompt_hash`、`duration_ms`、`final_chars`、`final_hash`、`answer_status` | `agent/orchestration.py` |
| `plan_step` | `plan_step`、`status=start/completed/failed`、`step_chars`、`duration_ms`、`result_chars`、`reason` | `agent/orchestration.py` |
| `round` | `round`、`round_global`、`status=start/end`、`max_rounds`、`duration_ms` | `agent/react_loop.py` |
| `model_request` | `component=planner/executor/summarizer`、`model`、`stream`、`message_count`、`tools_count`、`duration_ms`、`input_tokens`、`output_tokens`、`total_tokens`、`finish_reason`、`status` | `agent/planning.py`、`agent/request.py`、`agent/compaction.py` |
| `tool_call` | `tool`、`tool_call_id`、`call_index`、`args_chars`、`args_hash`、`args_preview`、`parallel_safe`、`scheduled_parallel`、`status=requested/rejected` | `agent/execution.py` phase 1 |
| `tool_result` | `tool`、`tool_call_id`、`call_index`、`duration_ms`、`raw_chars`、`result_chars`、`clipped`、`status=ok/error` | `agent/execution.py` phase 3 |
| `verification` | `check`、`status=pass/warn/fail`、`layer`、`reason` | `agent/react_loop.py`、`agent/compaction.py`、eval |
| `error` | `component`、`error_type`、`error`、`retryable` | `agent/request.py`、`agent/planning.py`、`agent/orchestration.py`、`agent/react_loop.py` |

### 1.3 开关与路径

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `MINI_AGENT_TRACE` | `0` | `1` 开启 JSONL trace |
| `MINI_AGENT_TRACE_DIR` | `logs` | trace 目录 |
| `MINI_AGENT_TRACE_MAX_BYTES` | `10485760` | 单文件上限，超出轮转 |
| `MINI_AGENT_TRACE_KEEP` | `3` | 保留几个旧文件 |
| `MINI_AGENT_TRACE_FIELD_CHARS` | `500` | 单字段截断 |
| `MINI_AGENT_TRACE_INCLUDE_CONTENT` | `0` | `1` 才写 prompt / 结果内容 |
| `MINI_AGENT_LOG` | `1` | `0` 关闭原文本日志（eval 用） |
| `MINI_AGENT_MAX_ROUNDS` | `100` | eval 的预算 override |
| `MINI_AGENT_MAX_OBS_CHARS` | `8000` | 同上 |
| `MINI_AGENT_MAX_TOOL_CALLS_PER_ROUND` | `20` | 同上 |

### 1.4 命令

```bash
MINI_AGENT_TRACE=1 python3 main.py --msg 'Summarize this project'
python3 eval/trace.py --file logs/trace_*.jsonl --check --summary

python3 eval/runner.py --suite smoke
python3 eval/runner.py --suite failure
python3 eval/runner.py --case single-tool-read --json
python3 eval/runner.py --case simple-final --record   # 需要 key
python3 eval/runner.py --case simple-final --replay   # 离线
```

退出码：`0` 全过；`1` 至少一个 case 断言失败；`2` harness / 配置错误（坏 JSON、脚本耗尽、录制缺失 / 过期、suite 为空）。

---

## 2. 改动前的基线

| 项 | 改动前 |
| --- | --- |
| 测试 | `_test.py` 8 个模块，模块导入即断言 |
| 日志 | 21 个 `EXPORT_LOG_CHOICES` gate，文本 section，无 schema |
| trace | 无；无 `trace_id` / `span_id` / 结构化事件 |
| eval | 无 `eval/`、无 fake model、无 case |
| 录制 | 无 |
| 预算 | `config.py` 硬编码，测试用 patch 模块全局 |

---

## 3. 工作机制

### 3.1 trace 写入

1. `ReActAgent.__init__` 调用 `tracing.get_tracer(...)`；`TRACE_ENABLED=0` 时得到 `NullTracer`。
2. 事件发射点调用 `self.tracer.new_span(parent)` 拿 span id，再 `self.tracer.emit(event, actor=..., span_id=..., ...)`。
3. `Tracer.emit` 先脱敏 / 截断，再 `json.dumps` 成一行，持锁 `os.write` 一次写入。
4. 写入前检查文件大小；超出 `max_bytes` 就把 `trace.jsonl` → `.1` → `.2` … 轮转。
5. 任何异常 → `enabled=False`，stderr 警告一次，**不抛出**。

### 3.2 线程模型

- `tool_call` 在 `_execute_tool_calls` 的 phase 1（主线程）发射。
- `tool_result` 在 phase 3（主线程）发射；worker 只返回 `(observation, duration)`。
- `Tracer` 内部仍有一把 `threading.Lock`，为未来的多 agent / 多写者防御。
- Ctrl-C 不捕获 `BaseException`；每行独立写，最多丢最后一行；到 `_end_round` 之前的已写事件仍是完整 JSON。

### 3.3 eval 流程

```text
case JSON
  → runner 注入 fake tools / 设置预算 / 创建 FakeClient
  → ReActAgent.run(prompt)（生产循环）
  → agent.messages + trace JSONL + FakeClient.requests
  → assertions.check_case(...)
  → 表格 / exit code
```

关键点：

- `FakeClient.create` 按 `stream` 和有无 `tools` 路由 planner / executor / summarizer。
- `tool_call` / `tool_calls` / `final` 脚本每次模型请求消费一条；耗尽即 harness error（exit 2）。
- 断言分六层：结构、轨迹、请求契约、结果、成本、trace 不变式。
- 请求契约（system prompt 片段、工具 schema 名称）是捕获 prompt / schema 回归的关键，因为脚本化模型不会对 prompt 变化作出反应。

### 3.4 config-by-value

`config.py` 的值在 import 时被各模块按值捕获。eval runner 在 import `agent` 前设置：

```python
os.environ.setdefault("MINI_AGENT_LOG", "0")
os.environ.setdefault("MINI_AGENT_TRACE", "1")
```

per-case 再 patch：

```python
agent.react_loop.MAX_ROUNDS
agent.execution.MAX_TOOL_CALLS_PER_ROUND
agent.execution.MAX_OBS_CHARS
utils.MAX_OBS_CHARS
```

`TRACE_PATH` 在 eval 中始终用绝对临时路径，避免 case 之间互相覆盖。

---

## 4. 逐文件代码

> 新增文件给出完整代码；修改文件给出 `a/`（改动前）到 `b/`（改动后）的 unified diff。diff 中 `+` 是新增，`-` 是删除。

### 4.1 新增 `tracing.py`

```python
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
```
### 4.2 修改 `config.py`

```diff
--- a/config.py
+++ b/config.py
@@ -14,7 +14,7 @@
 THINKING = os.getenv("DEEPSEEK_THINKING", "enabled")  # "enabled" or "disabled" — DeepSeek thinking mode is on by default; set to "disabled" to compare against a plain chat model
 
 # Request logging controls and per-event gates.
-EXPORT_LOG = True  # Set to False to disable logging of API requests and responses
+EXPORT_LOG = os.getenv("MINI_AGENT_LOG", "1") != "0"  # Set to False to disable logging of API requests and responses
 EXPORT_LOG_CHOICES = {
 	"initial-messages": False,
 	"message-beginning": True,
@@ -45,6 +45,18 @@
     EXPORT_LOG_NAME_TEMPLATE.format(current_time_str=current_time_str)
 )
 
+# Structured JSONL tracing. Disabled by default; eval turns it on explicitly.
+TRACE_ENABLED = os.getenv("MINI_AGENT_TRACE", "0") != "0"
+TRACE_DIR = os.getenv("MINI_AGENT_TRACE_DIR", "logs")
+TRACE_PATH = os.path.join(
+    TRACE_DIR,
+    f"trace_{current_time_str}_{os.getpid()}.jsonl"
+)
+TRACE_MAX_BYTES = int(os.getenv("MINI_AGENT_TRACE_MAX_BYTES", str(10 * 1024 * 1024)))
+TRACE_KEEP = int(os.getenv("MINI_AGENT_TRACE_KEEP", "3"))
+TRACE_FIELD_CHARS = int(os.getenv("MINI_AGENT_TRACE_FIELD_CHARS", "500"))
+TRACE_INCLUDE_CONTENT = os.getenv("MINI_AGENT_TRACE_INCLUDE_CONTENT", "0") == "1"
+
 # Terminal presentation. This affects display only, never stored history.
 RENDER_MARKDOWN = True  # Render the model's markdown as terminal styling (bold / italic / quote…); only takes effect on a tty — history and logs always keep the raw markdown
 
@@ -52,9 +64,9 @@
 MAX_API_RETRIES = 3  # SDK retries for retryable API failures
 API_TIMEOUT = 60	# Timeout for API requests in seconds
 
-MAX_ROUNDS = 100  # Maximum model/tool rounds for one ReAct step
-MAX_OBS_CHARS = 8000				# Maximum number of characters from tool output to be included in history per round
-MAX_TOOL_CALLS_PER_ROUND = 20		# Maximum number of tool calls allowed per round
+MAX_ROUNDS = int(os.getenv("MINI_AGENT_MAX_ROUNDS", "100"))  # Maximum model/tool rounds for one ReAct step
+MAX_OBS_CHARS = int(os.getenv("MINI_AGENT_MAX_OBS_CHARS", "8000"))				# Maximum number of characters from tool output to be included in history per round
+MAX_TOOL_CALLS_PER_ROUND = int(os.getenv("MINI_AGENT_MAX_TOOL_CALLS_PER_ROUND", "20"))		# Maximum number of tool calls allowed per round
 
 MAX_TOOL_RETRIES = 3
 TOOL_RETRY_BACKOFF = 0.5			# 0.5 seconds of exponential backoff for tool retries
```
### 4.3 修改 `agent/core.py`

```diff
--- a/agent/core.py
+++ b/agent/core.py
@@ -3,10 +3,13 @@
 import logging
 from openai import OpenAI
 import memory
+import tracing
 from config import (
     API_KEY, BASE_URL, MAX_API_RETRIES, API_TIMEOUT, DEFAULT_SYSTEM_PROMPT,
     EXPORT_LOG, EXPORT_LOG_PATH, MEMORY_INDEX_MAX_NOTES, MEMORY_INDEX_MAX_CHARS,
     MEMORY_INDEX_HEADER, PLAN_MAX_STEPS,
+    TRACE_ENABLED, TRACE_PATH, TRACE_MAX_BYTES, TRACE_KEEP,
+    TRACE_FIELD_CHARS, TRACE_INCLUDE_CONTENT,
 )
 from .planning import PlanningMixin
 from .compaction import CompactionMixin
@@ -23,16 +26,24 @@
 	It can call tools based on the model's responses and return the results to the model for further processing. 
 	The agent can also log its interactions for debugging or analysis purposes.
 	'''
-	def __init__(self, system_prompt: str = DEFAULT_SYSTEM_PROMPT):
+	def __init__(self, system_prompt: str = DEFAULT_SYSTEM_PROMPT,
+	             client=None, tracer=None):
 		'''
 		Initialize the ReActAgent with a system prompt.
 
 		Args:
 			system_prompt (str): The system prompt to guide the agent's behavior.
+			client: Optional OpenAI-compatible client; tests and eval inject a fake.
+			tracer: Optional tracer; None means build one from config.
 		'''
-		self.client = OpenAI(
+		self.client = client if client is not None else OpenAI(
 			api_key=API_KEY, base_url=BASE_URL, max_retries=MAX_API_RETRIES, timeout=API_TIMEOUT
 		)
+		self.tracer = tracer if tracer is not None else tracing.get_tracer(
+			enabled=TRACE_ENABLED, path=TRACE_PATH,
+			max_bytes=TRACE_MAX_BYTES, keep=TRACE_KEEP,
+			max_field_chars=TRACE_FIELD_CHARS,
+			include_content=TRACE_INCLUDE_CONTENT)
 		self.system_prompt = system_prompt
 		self.messages = []
 		self.plan = []
@@ -44,6 +55,13 @@
 		self.memory_index_error = None   # why it is empty, when that was not simply "no notes"
 		self.plan_request = None
 		self.plan_error = None
+		self._turn_span = None
+		self._turn_started = 0.0
+		self._plan_step = None
+		self._plan_step_span = None
+		self._round = 0
+		self._round_started = 0.0
+		self._round_span = None
 
 
 		if EXPORT_LOG:
@@ -93,5 +111,12 @@
 		self.plan_request = None
 		self.plan_error = None
 		self._rounds_used = 0
+		self._turn_span = None
+		self._turn_started = 0.0
+		self._plan_step = None
+		self._plan_step_span = None
+		self._round = 0
+		self._round_started = 0.0
+		self._round_span = None
```
### 4.4 修改 `agent/orchestration.py`

```diff
--- a/agent/orchestration.py
+++ b/agent/orchestration.py
@@ -1,7 +1,9 @@
 """Execute a user request directly or supervise its ordered plan steps."""
 
+import time
 from config import EXPORT_LOG, EXPORT_LOG_CHOICES, EXPORT_LOG_PATH
 from presentation import PlanProgressDisplay
+from tracing import hash_text
 
 class TaskOrchestrationMixin:
 	"""Route requests through planning and coordinate each planned step."""
@@ -22,6 +24,17 @@
 		self.plan_request = prompt
 		self.plan_error = None
 		self.plan = []
+		self._turn_started = time.monotonic()
+		self._turn_span = self.tracer.new_span()
+		self._plan_step = None
+		self._plan_step_span = None
+		self._round = 0
+		self._round_started = 0.0
+		self._round_span = None
+		self.tracer.emit(
+			"turn", actor="orchestrator", span_id=self._turn_span,
+			parent_span_id=None, turn=self.turn, status="start",
+			prompt_chars=len(prompt), prompt_hash=hash_text(prompt))
 		self._refresh_plan_context()
 
 		if EXPORT_LOG and EXPORT_LOG_CHOICES["turn"]:
@@ -36,7 +49,9 @@
 
 		if not needs_plan:
 			self._refresh_plan_context()
-			return self._run_react(prompt, first_turn=first_turn)
+			result = self._run_react(prompt, first_turn=first_turn)
+			status = "failed" if self._execution_failed(result) else "ok"
+			return self._end_turn(result, status)
 
 		self.plan = [{"text": step, "status": "pending"} for step in steps]
 		self._refresh_plan_context()
@@ -53,6 +68,15 @@
 		try:
 			for i, step in enumerate(self.plan):
 				step["status"] = "in_progress"
+				self._plan_step = i + 1
+				self._plan_step_span = self.tracer.new_span(self._turn_span)
+				step_started = time.monotonic()
+				self.tracer.emit(
+					"plan_step", actor="orchestrator",
+					span_id=self._plan_step_span,
+					parent_span_id=self._turn_span, turn=self.turn,
+					plan_step=self._plan_step, status="start",
+					step_chars=len(step["text"]))
 				self._refresh_plan_context()
 				self._plan_display.update(self.plan)
 				print(f"\n--- Plan step {i + 1}/{len(self.plan)} ---")
@@ -85,7 +109,20 @@
 					message = (f"Plan stopped at step {i + 1}/{len(self.plan)}: "
 						f"{type(e).__name__}: {e}")
 					self._log_section("planning", message)
-					return message
+					self.tracer.emit(
+						"plan_step", actor="orchestrator",
+						span_id=self._plan_step_span,
+						parent_span_id=self._turn_span, turn=self.turn,
+						plan_step=self._plan_step, status="failed",
+						duration_ms=round((time.monotonic() - step_started) * 1000, 1),
+						reason=message[:500])
+					self.tracer.emit(
+						"error", actor="orchestrator",
+						span_id=self._plan_step_span,
+						parent_span_id=self._turn_span, turn=self.turn,
+						component="plan", error_type=type(e).__name__,
+						error=message[:500], retryable=False)
+					return self._end_turn(message, "error")
 
 				results.append(result)
 				if self._execution_failed(result):
@@ -95,14 +132,45 @@
 					message = (f"Plan stopped at step {i + 1}/{len(self.plan)}. "
 						f"Execution result: {result}")
 					self._log_section("planning", message)
-					return message
+					self.tracer.emit(
+						"plan_step", actor="orchestrator",
+						span_id=self._plan_step_span,
+						parent_span_id=self._turn_span, turn=self.turn,
+						plan_step=self._plan_step, status="failed",
+						duration_ms=round((time.monotonic() - step_started) * 1000, 1),
+						reason=message[:500])
+					self.tracer.emit(
+						"error", actor="orchestrator",
+						span_id=self._plan_step_span,
+						parent_span_id=self._turn_span, turn=self.turn,
+						component="plan", error_type="PlanStepFailed",
+						error=message[:500], retryable=False)
+					return self._end_turn(message, "failed")
 
 				step["status"] = "completed"
 				self._refresh_plan_context()
 				self._plan_display.update(self.plan)
 				self._log_section("planning", f"Step {i + 1} completed: {step['text']}")
-			return results[-1] if results else "No plan steps were executed."
+				self.tracer.emit(
+					"plan_step", actor="orchestrator",
+					span_id=self._plan_step_span,
+					parent_span_id=self._turn_span, turn=self.turn,
+					plan_step=self._plan_step, status="completed",
+					duration_ms=round((time.monotonic() - step_started) * 1000, 1),
+					result_chars=len(result))
+			return self._end_turn(
+				results[-1] if results else "No plan steps were executed.")
 		finally:
 			self._plan_display.close()
 
+	def _end_turn(self, result: str, status: str = "ok") -> str:
+		"""Emit the turn-end event once and return the result unchanged."""
+		self.tracer.emit(
+			"turn", actor="orchestrator", span_id=self._turn_span,
+			parent_span_id=None, turn=self.turn, status="end",
+			duration_ms=round((time.monotonic() - self._turn_started) * 1000, 1),
+			final_chars=len(result), final_hash=hash_text(result),
+			answer_status=status)
+		return result
+
```
### 4.5 修改 `agent/planning.py`

```diff
--- a/agent/planning.py
+++ b/agent/planning.py
@@ -1,15 +1,19 @@
 """Planner request, plan validation, and plan status rendering."""
 
+import time
 from config import (
     MODEL, PLAN_PROMPT, PLAN_MAX_TOKENS, THINKING, REQUEST_USAGE,
     MEMORY_INDEX_HEADER,
 )
+from tracing import hash_text
 from .helpers import _parse_plan_response
 
 class PlanningMixin:
 	"""Group related behavior for planning."""
 	def _request_plan(self, prompt: str) -> tuple:
 		"""Classify a request and, when needed, return its ordered steps."""
+		started = time.monotonic()
+		span = self.tracer.new_span(self._turn_span)
 		try:
 			print("🧭 Planning request…", flush=True)
 			request = {
@@ -29,9 +33,30 @@
 			if msg.usage is not None:
 				self.last_usage = msg.usage
 			needs_plan, steps, error = _parse_plan_response(msg.content)
+			usage = getattr(msg, "usage", None)
+			self.tracer.emit(
+				"model_request", actor="planner", span_id=span,
+				parent_span_id=self._turn_span, component="planner",
+				model=MODEL, stream=True, message_count=2, tools_count=0,
+				request_chars=len(prompt), prompt_hash=hash_text(prompt),
+				duration_ms=round((time.monotonic() - started) * 1000, 1),
+				input_tokens=getattr(usage, "prompt_tokens", None),
+				output_tokens=getattr(usage, "completion_tokens", None),
+				total_tokens=getattr(usage, "total_tokens", None),
+				finish_reason=getattr(msg, "finish_reason", None),
+				status="ok")
 		except Exception as e:
+			self.tracer.emit(
+				"error", actor="planner", span_id=span,
+				parent_span_id=self._turn_span, component="planner",
+				error_type=type(e).__name__, error=str(e)[:500],
+				retryable=False)
 			return False, [], f"Planner request failed: {type(e).__name__}: {e}"
 		if error:
+			self.tracer.emit(
+				"verification", actor="planner", span_id=span,
+				parent_span_id=self._turn_span, check="plan_schema",
+				status="fail", layer="structure", reason=error[:500])
 			return False, [], f"Planner response rejected: {error}"
 		return needs_plan, steps, ""
```
### 4.6 修改 `agent/react_loop.py`

```diff
--- a/agent/react_loop.py
+++ b/agent/react_loop.py
@@ -1,6 +1,7 @@
 """Single-step ReAct request, tool-call, and response loop."""
 
 import json
+import time
 from config import (
     MAX_ROUNDS, EXPORT_LOG, EXPORT_LOG_CHOICES, EXPORT_LOG_PATH,
     MEMORY_INDEX_MAX_NOTES, MEMORY_INDEX_MAX_CHARS,
@@ -68,6 +69,16 @@
 		while round < max_rounds:
 			round += 1
 			self._rounds_used += 1
+			self._round = round
+			self._round_started = time.monotonic()
+			parent = self._plan_step_span or self._turn_span
+			self._round_span = self.tracer.new_span(parent)
+			self.tracer.emit(
+				"round", actor="react", span_id=self._round_span,
+				parent_span_id=parent, turn=self.turn,
+				plan_step=self._plan_step, round=round,
+				round_global=self._rounds_used, status="start",
+				max_rounds=max_rounds)
 			print(f"--- Round {round} ---")
 
 			if EXPORT_LOG:
@@ -85,6 +96,7 @@
             # LLM Decision
 			msg, request_error = self._request_completion(round)
 			if request_error:
+				self._end_round("error", error_type="ModelRequestError")
 				return request_error
 			content = msg.content or ""
 
@@ -101,6 +113,13 @@
 			# normal shape, and _answer_warning assumes a closing reply.
 			if not msg.tool_calls:
 				warning = _answer_warning(content, msg.finish_reason)
+				self.tracer.emit(
+					"verification", actor="react", span_id=self._round_span,
+					parent_span_id=self._plan_step_span or self._turn_span,
+					turn=self.turn, plan_step=self._plan_step,
+					check="answer_shape",
+					status="warn" if warning else "pass",
+					layer="result", reason=(warning or "")[:500])
 				if warning:
 					print(warning)
 					if EXPORT_LOG and EXPORT_LOG_CHOICES["llm-finish-reason"]:
@@ -133,12 +152,14 @@
 						f.write(content)
 						f.write("\n\n")
 				self._append_assistant_reply(msg, content)
+				self._end_round("ok", answer="final")
 				return content.split("Final Answer:")[-1].strip()
             
             # No tool call -> return the content
 			if not msg.tool_calls:
 				print("[Done]")
 				self._append_assistant_reply(msg, content)
+				self._end_round("ok", answer="plain")
 				return content
 
             # Note that the assistant message should be constructed outside of the tool execution loop
@@ -175,6 +196,16 @@
 					f.write(json.dumps(self.messages, indent=4))
 					f.write("\n\n")
 
+			self._end_round("ok")
+
+		error_parent = self._plan_step_span or self._turn_span
+		error_span = self.tracer.new_span(error_parent)
+		self.tracer.emit(
+			"error", actor="react", span_id=error_span,
+			parent_span_id=error_parent,
+			turn=self.turn, plan_step=self._plan_step,
+			component="react", error_type="MaxRounds",
+			error="Max steps reached", retryable=False)
 		if EXPORT_LOG:
 			with open(EXPORT_LOG_PATH, "a") as f:
 				f.write(f"Max steps reached. Exiting.\n")
@@ -182,3 +213,18 @@
 
 		return "Max steps reached"
 
+	def _end_round(self, status: str = "ok", **fields):
+		"""Emit the round-end event; tolerate direct _run_react tests with no span."""
+		span = self._round_span
+		if span is None:
+			return
+		self._round_span = None
+		self.tracer.emit(
+			"round", actor="react", span_id=span,
+			parent_span_id=self._plan_step_span or self._turn_span,
+			turn=self.turn, plan_step=self._plan_step,
+			round=self._round, round_global=self._rounds_used,
+			status="end", duration_ms=round(
+				(time.monotonic() - self._round_started) * 1000, 1),
+			**fields)
+
```
### 4.7 修改 `agent/request.py`

```diff
--- a/agent/request.py
+++ b/agent/request.py
@@ -1,6 +1,7 @@
 """Model request construction, streaming, and response metadata logging."""
 
 import json
+import time
 from openai import (
     BadRequestError, APIConnectionError, RateLimitError, InternalServerError,
 )
@@ -21,6 +22,10 @@
 		Returns:
 			A (message, error) pair; exactly one item is non-empty.
 		"""
+		started = time.monotonic()
+		span = self.tracer.new_span(self._round_span or self._turn_span)
+		request_chars = sum(
+			len(str(m.get("content") or "")) for m in self.messages)
 		request = {
 			"model": MODEL,
 			"messages": self.messages,
@@ -51,6 +56,12 @@
 				with open(EXPORT_LOG_PATH, "a") as f:
 					f.write(msg + "\n")
 					f.write("\n\n")
+			self.tracer.emit(
+				"error", actor="react", span_id=span,
+				parent_span_id=self._round_span or self._turn_span,
+				turn=self.turn, plan_step=self._plan_step,
+				component="executor", error_type=type(e).__name__,
+				error=str(e)[:500], retryable=False)
 			return None, msg
 		except (APIConnectionError, RateLimitError, InternalServerError) as e:
 			# Implying that the error is temporary, but the SDK has already retried with its own max_retries, so we won't retry here.
@@ -61,9 +72,15 @@
 				with open(EXPORT_LOG_PATH, "a") as f:
 					f.write(msg + "\n")
 					f.write("\n\n")
+			self.tracer.emit(
+				"error", actor="react", span_id=span,
+				parent_span_id=self._round_span or self._turn_span,
+				turn=self.turn, plan_step=self._plan_step,
+				component="executor", error_type=type(e).__name__,
+				error=str(e)[:500], retryable=True)
 			return None, msg
 		except Exception as e:
-			# Other unexpected errors during the LLM call. 
+			# Other unexpected errors during the LLM call.
 			# This could be due to various reasons, including network issues or unexpected responses from the API.
 			msg = f"Error during LLM call ({type(e).__name__}): {e}"
 			print(msg)
@@ -71,6 +88,12 @@
 				with open(EXPORT_LOG_PATH, "a") as f:
 					f.write(msg + "\n")
 					f.write("\n\n")
+			self.tracer.emit(
+				"error", actor="react", span_id=span,
+				parent_span_id=self._round_span or self._turn_span,
+				turn=self.turn, plan_step=self._plan_step,
+				component="executor", error_type=type(e).__name__,
+				error=str(e)[:500], retryable=False)
 			return None, msg
 				
 
@@ -114,4 +137,19 @@
 				f.write(json.dumps(_message_dict(msg), indent=4))
 				f.write("\n\n")
 
+		usage = getattr(msg, "usage", None)
+		self.tracer.emit(
+			"model_request", actor="react", span_id=span,
+			parent_span_id=self._round_span or self._turn_span,
+			turn=self.turn, plan_step=self._plan_step,
+			component="executor", model=MODEL, stream=True,
+			message_count=len(self.messages), tools_count=len(TOOLS_DESC),
+			request_chars=request_chars,
+			duration_ms=round((time.monotonic() - started) * 1000, 1),
+			input_tokens=getattr(usage, "prompt_tokens", None),
+			output_tokens=getattr(usage, "completion_tokens", None),
+			total_tokens=getattr(usage, "total_tokens", None),
+			finish_reason=getattr(msg, "finish_reason", None),
+			status="ok")
+
 		return msg, ""
```
### 4.8 修改 `agent/execution.py`

```diff
--- a/agent/execution.py
+++ b/agent/execution.py
@@ -14,6 +14,7 @@
 from tools import TOOLS, TOOL_META
 from utils import clip
 from presentation import MarkdownStream, rendering_enabled
+import tracing
 
 RETRIABLE_ERRORS = (subprocess.TimeoutExpired, ConnectionError, TimeoutError)
 
@@ -147,6 +148,25 @@
 			plans.append({"i": i, "tc": tc, "name": name, "args": args,
 			              "error": error, "safe": safe})
 
+		# Phase 1b: one trace span per call, emitted on the caller thread.
+		for plan in plans:
+			plan["span_id"] = self.tracer.new_span(self._round_span)
+			raw_args = plan["tc"].function.arguments or ""
+			self.tracer.emit(
+				"tool_call", actor=f"tool:{plan['name']}",
+				span_id=plan["span_id"],
+				parent_span_id=self._round_span, turn=self.turn,
+				plan_step=self._plan_step, round=self._round,
+				round_global=self._rounds_used,
+				tool=plan["name"], tool_call_id=plan["tc"].id,
+				call_index=plan["i"] + 1, args_chars=len(raw_args),
+				args_hash=tracing.hash_text(raw_args),
+				args_preview=tracing.redact_args(plan["args"] or {}),
+				parallel_safe=self._parallel_safe(plan["name"]),
+				scheduled_parallel=plan["safe"],
+				status="rejected" if plan["error"] else "requested",
+				error_type="Rejected" if plan["error"] else None)
+
 		# Phase 2: contiguous safe runs; error/unsafe are one-element barriers.
 		groups, run = [], []
 		for plan in plans:
@@ -215,6 +235,17 @@
 			label = f"[{k}/{n}] " if n > 1 else ""
 			print(f"👁 Observation: {label}{observation[:200]}"
 			      f"{'...' if len(observation) > 200 else ''}")
+			self.tracer.emit(
+				"tool_result", actor=f"tool:{plan['name']}",
+				span_id=plan["span_id"],
+				parent_span_id=self._round_span, turn=self.turn,
+				plan_step=self._plan_step, round=self._round,
+				round_global=self._rounds_used,
+				tool=plan["name"], tool_call_id=plan["tc"].id,
+				call_index=k, duration_ms=round(duration * 1000, 1),
+				raw_chars=raw_len, result_chars=len(observation),
+				clipped=raw_len > len(observation),
+				status="error" if observation.startswith("Error:") else "ok")
 			messages.append({"role": "tool", "tool_call_id": plan["tc"].id,
 			                 "content": observation})
 		return messages
```
### 4.9 修改 `agent/compaction.py`

```diff
--- a/agent/compaction.py
+++ b/agent/compaction.py
@@ -1,5 +1,6 @@
 """Context occupancy reporting and transactional history compaction."""
 
+import time
 from config import (
     CONTEXT_WINDOW_TOKENS, EXPORT_LOG, EXPORT_LOG_CHOICES, EXPORT_LOG_PATH,
     COMPACT_KEEP_TURNS, COMPACT_KEEP_GROUPS, COMPACT_SUMMARY_MAX_TOKENS,
@@ -72,17 +73,36 @@
 						 "forward:\n" + previous)
 		parts.append("Transcript to summarize:\n" + transcript)
 
-		response = self.client.chat.completions.create(
-			model=MODEL,
-			messages=[
-				{"role": "system", "content": COMPACT_SUMMARY_PROMPT},
-				{"role": "user", "content": "\n\n---\n\n".join(parts)},
-			],
-			max_tokens=COMPACT_SUMMARY_MAX_TOKENS,
-			stream=False,
-			extra_body={"thinking": {"type": "disabled"}},
-		)
-		return (response.choices[0].message.content or "").strip()
+		started = time.monotonic()
+		span = self.tracer.new_span(self._turn_span)
+		request_chars = sum(len(part) for part in parts)
+		try:
+			response = self.client.chat.completions.create(
+				model=MODEL,
+				messages=[
+					{"role": "system", "content": COMPACT_SUMMARY_PROMPT},
+					{"role": "user", "content": "\n\n---\n\n".join(parts)},
+				],
+				max_tokens=COMPACT_SUMMARY_MAX_TOKENS,
+				stream=False,
+				extra_body={"thinking": {"type": "disabled"}},
+			)
+		except Exception as e:
+			self.tracer.emit(
+				"error", actor="compactor", span_id=span,
+				parent_span_id=self._turn_span, component="summarizer",
+				error_type=type(e).__name__, error=str(e)[:500],
+				retryable=False)
+			raise
+		content = (response.choices[0].message.content or "").strip()
+		self.tracer.emit(
+			"model_request", actor="compactor", span_id=span,
+			parent_span_id=self._turn_span, component="summarizer",
+			model=MODEL, stream=False, message_count=2, tools_count=0,
+			request_chars=request_chars,
+			duration_ms=round((time.monotonic() - started) * 1000, 1),
+			status="ok")
+		return content
 
 	def _summarize(self, transcript: str, on_chunk=None) -> str:
 		'''Summarize a transcript, folding it through several calls if needed.
@@ -166,6 +186,12 @@
 		candidate = build_compacted(self.messages, cut, summary)
 
 		problems = validate_history(candidate)
+		self.tracer.emit(
+			"verification", actor="compactor",
+			span_id=self.tracer.new_span(self._turn_span),
+			parent_span_id=self._turn_span, check="compaction_history",
+			status="fail" if problems else "pass", layer="structure",
+			reason="; ".join(problems[:3])[:500])
 		if problems:
 			self._log_section("compact", "Compact REJECTED — illegal history:\n  "
 							  + "\n  ".join(problems))
```
### 4.10 修改 `main.py`

```diff
--- a/main.py
+++ b/main.py
@@ -10,6 +10,7 @@
 # Only USAGE_TEXT reads these four — it reports the real defaults rather than
 # restating them, so the help cannot drift from config.py.
 from config import MODEL, THINKING, BASE_URL, CONTEXT_WINDOW_TOKENS
+from config import TRACE_ENABLED, TRACE_PATH
 from context import format_context_line
 from agent import ReActAgent
 from tools import FAILED
@@ -74,6 +75,8 @@
   DEEPSEEK_BASE_URL         default {BASE_URL}
   DEEPSEEK_CONTEXT_WINDOW   token budget for the occupancy display (default {CONTEXT_WINDOW_TOKENS:,})
   PARALLEL_ENABLED          0 disables parallel tool execution (default enabled)
+  MINI_AGENT_TRACE          1 enables JSONL tracing (default 0)
+  MINI_AGENT_TRACE_DIR      trace directory (default logs)
 
 In the REPL, type /help for the command list (/compact, /context, /memory, /exit).
 
@@ -228,6 +231,8 @@
 			os.makedirs(EXPORT_LOG_DIR)
 
 	agent = ReActAgent()
+	if TRACE_ENABLED:
+		print(f"Trace: {os.path.abspath(TRACE_PATH)}")
 
 	# A prompt means one-shot mode: run once and exit. The answer is streamed inside
 	# run(), so it is not printed a second time here.
```
### 4.11 新增 `eval/__init__.py`

```python
"""Offline eval harness for the mini agent. Not imported by production code."""
```
### 4.12 新增 `eval/fake_model.py`

```python
"""Scripted model and fake tools used by the offline eval harness."""

import json
from types import SimpleNamespace


def fake_chunk(content=None, reasoning=None, finish=None, usage=None,
               with_choices=True):
    """Build the minimal chunk shape consumed by ExecutionMixin._consume_stream."""
    delta = SimpleNamespace(content=content, reasoning_content=reasoning,
                            tool_calls=None)
    choices = ([SimpleNamespace(delta=delta, finish_reason=finish)]
               if with_choices else [])
    return SimpleNamespace(choices=choices, usage=usage)


def tool_call_chunk(index, call_id, name, arguments):
    """One tool-call fragment; _consume_stream joins fragments by index."""
    tc = SimpleNamespace(
        index=index, id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=arguments))
    delta = SimpleNamespace(content=None, reasoning_content=None,
                            tool_calls=[tc])
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=None)],
        usage=None)


def usage_obj(prompt_tokens=10, completion_tokens=3):
    return SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens)


class FakeClient:
    """A scripted replacement for OpenAI's chat.completions client.

    Routing:
      stream=True  and no `tools`  -> planner
      stream=True  and has `tools` -> ReAct executor
      stream=False                -> summarizer

    The executor consumes one script entry per model round. A `tool_calls`
    entry produces one round with several calls; a `tool_call` entry produces
    one round with one call; `final` closes the loop.
    """

    def __init__(self, case):
        self.case = case
        self.requests = []
        self.script = list(case.get("script", []))
        self.max_retries = 0
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        stream = bool(kwargs.get("stream"))
        if stream and "tools" not in kwargs:
            return iter(self._planner_stream())
        if stream and "tools" in kwargs:
            return iter(self._executor_stream())
        return self._summarizer_response()

    def _planner_stream(self):
        plan = self.case.get("plan", {"needs_plan": False, "steps": []})
        content = plan["raw"] if "raw" in plan else json.dumps(
            plan, ensure_ascii=False)
        yield fake_chunk(content=content)
        yield fake_chunk(content="", finish="stop", usage=usage_obj())

    def _executor_stream(self):
        if not self.script:
            raise RuntimeError("script exhausted: no more model responses")
        step = self.script.pop(0)
        kind = step.get("type")
        if kind == "tool_call":
            yield tool_call_chunk(
                0, step.get("id", self._call_id()), step["name"],
                step.get("arguments_raw",
                         json.dumps(step.get("arguments", {}),
                                    ensure_ascii=False)))
            yield fake_chunk(content="", finish="tool_calls",
                             usage=usage_obj())
            return
        if kind == "tool_calls":
            for index, call in enumerate(step.get("calls", [])):
                yield tool_call_chunk(
                    index, call.get("id", self._call_id()), call["name"],
                    call.get("arguments_raw",
                             json.dumps(call.get("arguments", {}),
                                        ensure_ascii=False)))
            yield fake_chunk(content="", finish="tool_calls",
                             usage=usage_obj())
            return
        if kind == "final":
            yield fake_chunk(content=step.get("content", ""))
            yield fake_chunk(content="", finish="stop", usage=usage_obj())
            return
        if kind == "raise":
            raise RuntimeError(step.get("error", "scripted failure"))
        raise RuntimeError(f"unknown script step: {kind!r}")

    def _summarizer_response(self):
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(
                content=self.case.get("summary", "Summary of earlier conversation.")))])

    def _call_id(self):
        return f"call_{len(self.requests)}"
```
### 4.13 新增 `eval/assertions.py`

```python
"""Assertion layers for offline eval cases.

Each check returns a list of human-readable failures. The runner turns an
empty list into PASS and a non-empty list into FAIL.
"""

import json
import re


def _tool_calls(messages):
    calls = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for tc in message.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except (KeyError, json.JSONDecodeError):
                args = {}
            calls.append({
                "name": tc["function"]["name"],
                "arguments": args,
                "id": tc.get("id"),
            })
    return calls


def _subset(expected, actual):
    return all(actual.get(key) == value for key, value in expected.items())


def _check_request_contract(requests, expect):
    failures = []
    contract = expect.get("request") or {}
    planners = [r for r in requests if r.get("stream") and "tools" not in r]
    executors = [r for r in requests if r.get("stream") and "tools" in r]

    if "planner_called" in contract:
        if bool(planners) != bool(contract["planner_called"]):
            failures.append(
                f"request.planner_called expected {contract['planner_called']}, "
                f"got {bool(planners)}")
    if "system_prompt_contains" in contract:
        if not executors:
            failures.append("request.system_prompt_contains: no executor request")
        else:
            system = executors[-1]["messages"][0].get("content") or ""
            for needle in contract["system_prompt_contains"]:
                if needle not in system:
                    failures.append(
                        f"system prompt missing {needle!r}")
    if "tool_schema_names" in contract:
        if not executors:
            failures.append("request.tool_schema_names: no executor request")
        else:
            names = {tool["function"]["name"] for tool in executors[-1]["tools"]}
            for name in contract["tool_schema_names"]:
                if name not in names:
                    failures.append(f"tool schema missing {name!r}")
    return failures


def _check_trace(trace_events, expect):
    failures = []
    if not trace_events:
        return failures

    spans = {e.get("span_id") for e in trace_events}
    calls = {e.get("tool_call_id") for e in trace_events
             if e.get("event") == "tool_call"}
    results = {e.get("tool_call_id") for e in trace_events
               if e.get("event") == "tool_result"}

    if expect.get("trace_parent_links", True):
        for event in trace_events:
            parent = event.get("parent_span_id")
            if parent is not None and parent not in spans:
                failures.append(
                    f"trace: {event.get('event')} has unknown parent {parent}")
                break
        if calls - results:
            failures.append(f"trace: calls without results: {sorted(calls - results)}")
        if results - calls:
            failures.append(f"trace: results without calls: {sorted(results - calls)}")

    counts = expect.get("trace_event_counts")
    if counts:
        actual = {}
        for event in trace_events:
            actual[event["event"]] = actual.get(event["event"], 0) + 1
        for name, wanted in counts.items():
            if actual.get(name, 0) != wanted:
                failures.append(
                    f"trace event {name}: expected {wanted}, got {actual.get(name, 0)}")
    return failures


def check_case(agent, case, result, requests, trace_events=None):
    """Return a list of failures; [] means the case passed."""
    failures = []
    expect = case.get("expect", {})
    trace_events = trace_events or []

    # 1. Structure: the history protocol must hold.
    if expect.get("history_valid", True):
        from context import validate_history
        problems = validate_history(agent.messages)
        if problems:
            failures.append("history invalid: " + "; ".join(problems[:3]))

    # 2. Trajectory: tool names, argument subsets, call count.
    calls = _tool_calls(agent.messages)
    if "tool_sequence" in expect:
        actual = [call["name"] for call in calls]
        if actual != expect["tool_sequence"]:
            failures.append(
                f"tool_sequence expected {expect['tool_sequence']}, got {actual}")
    if "tool_args" in expect:
        for index, wanted in enumerate(expect["tool_args"]):
            if index >= len(calls):
                failures.append(f"tool_args: missing call #{index + 1}")
            elif not _subset(wanted, calls[index]["arguments"]):
                failures.append(
                    f"tool_args #{index + 1}: expected subset {wanted}, "
                    f"got {calls[index]['arguments']}")
    if "tool_call_count" in expect and len(calls) != expect["tool_call_count"]:
        failures.append(
            f"tool_call_count expected {expect['tool_call_count']}, got {len(calls)}")

    tool_messages = [m for m in agent.messages if m.get("role") == "tool"]
    contents = [m.get("content") or "" for m in tool_messages]
    if expect.get("no_tool_errors"):
        bad = [c[:120] for c in contents if c.startswith("Error:")]
        if bad:
            failures.append(f"tool errors: {bad}")
    if "tool_results_contain" in expect:
        for needle in expect["tool_results_contain"]:
            if not any(needle in content for content in contents):
                failures.append(f"no tool result contains {needle!r}")

    # 3. Request contract: catches prompt / tool-schema regressions, which a
    # scripted model cannot notice by itself.
    failures.extend(_check_request_contract(requests, expect))

    # 4. Result: prefer contains / regex over exact equality.
    if "final_contains" in expect:
        for needle in expect["final_contains"]:
            if needle not in (result or ""):
                failures.append(f"final answer missing {needle!r}")
    if "final_regex" in expect:
        if not re.search(expect["final_regex"], result or "", re.S):
            failures.append(f"final answer did not match {expect['final_regex']!r}")
    if "final_equals" in expect and result != expect["final_equals"]:
        failures.append(f"final answer expected exactly {expect['final_equals']!r}")

    # 5. Cost: rounds, model requests, tokens.
    if "max_rounds" in expect and agent._rounds_used > expect["max_rounds"]:
        failures.append(
            f"rounds {agent._rounds_used} > max_rounds {expect['max_rounds']}")
    if "max_model_requests" in expect and len(requests) > expect["max_model_requests"]:
        failures.append(
            f"model requests {len(requests)} > max_model_requests "
            f"{expect['max_model_requests']}")
    if "max_total_tokens" in expect:
        total = sum(event.get("total_tokens") or 0 for event in trace_events
                    if event.get("event") == "model_request")
        if total > expect["max_total_tokens"]:
            failures.append(
                f"total tokens {total} > max_total_tokens "
                f"{expect['max_total_tokens']}")

    # 6. Trace invariants.
    failures.extend(_check_trace(trace_events, expect))
    return failures
```
### 4.14 新增 `eval/runner.py`

```python
"""Offline eval runner.

Usage:
    python3 eval/runner.py --suite smoke
    python3 eval/runner.py --case simple-final
    python3 eval/runner.py --json
    python3 eval/runner.py --case simple-final --record
    python3 eval/runner.py --case simple-final --replay
"""

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import traceback
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from io import StringIO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# These must be set before the first import of config / agent: config captures
# them by value at import time.
os.environ.setdefault("MINI_AGENT_LOG", "0")
os.environ.setdefault("MINI_AGENT_TRACE", "1")

from eval.assertions import check_case
from eval.fake_model import FakeClient

CASES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases")


@dataclass
class CaseResult:
    case_id: str
    passed: bool = False
    harness_error: bool = False
    failures: list = field(default_factory=list)
    rounds: int = 0
    tools: int = 0
    tokens: int = 0
    duration_ms: float = 0.0
    trace_path: str = ""


def load_cases(suite=None, case_id=None):
    cases = []
    for name in sorted(os.listdir(CASES_DIR)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(CASES_DIR, name), encoding="utf-8") as f:
            case = json.load(f)
        if case_id and case.get("id") != case_id:
            continue
        if suite and case.get("suite") != suite:
            continue
        cases.append(case)
    return cases


def make_tool(name, spec, shared_barrier=None):
    """Turn one JSON tool spec into a callable; keep it thread-safe."""
    if isinstance(spec, str):
        return lambda **_: spec
    if not isinstance(spec, dict):
        return lambda **_: ""
    mode = spec.get("mode")
    if mode == "shared_barrier" and shared_barrier is not None:
        result = spec.get("result", "ok")

        def shared_tool(**_):
            shared_barrier.wait()
            return result
        return shared_tool
    if mode == "barrier":
        barrier = threading.Barrier(
            int(spec.get("parties", 2)), timeout=float(spec.get("timeout", 2)))
        result = spec.get("result", "ok")

        def barrier_tool(**_):
            barrier.wait()
            return result
        return barrier_tool
    if "error" in spec:
        error = spec["error"]

        def raising_tool(**_):
            raise RuntimeError(error)
        return raising_tool
    result = spec.get("result", "")

    def fixed_tool(**_):
        return result
    return fixed_tool


def read_trace(path):
    if not os.path.exists(path):
        return []
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return events


def run_case(case, trace_dir, record=False, replay=False):
    started = time.monotonic()
    trace_path = os.path.join(trace_dir, f"{case['id']}.jsonl")
    if os.path.exists(trace_path):
        os.remove(trace_path)
    rec_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "recordings",
        f"{case['id']}.jsonl")

    from agent import ReActAgent
    from tools import TOOLS, TOOL_META
    from tracing import Tracer, NullTracer
    import agent.react_loop as react_loop
    import agent.execution as execution
    import utils

    added = []
    old_tools, old_meta = {}, {}
    old_limits = (react_loop.MAX_ROUNDS, execution.MAX_TOOL_CALLS_PER_ROUND,
                  execution.MAX_OBS_CHARS, utils.MAX_OBS_CHARS)
    limits = case.get("limits") or {}
    barrier_spec = case.get("shared_barrier")
    shared_barrier = (threading.Barrier(
        int(barrier_spec.get("parties", 2)),
        timeout=float(barrier_spec.get("timeout", 2)))
        if barrier_spec else None)

    try:
        for name, spec in (case.get("tools") or {}).items():
            old_tools[name] = TOOLS.get(name)
            old_meta[name] = TOOL_META.get(name)
            TOOLS[name] = make_tool(name, spec, shared_barrier)
            TOOL_META[name] = {
                "parallel_safe": bool(spec.get("parallel_safe", False))
                if isinstance(spec, dict) else False}
            added.append(name)

        react_loop.MAX_ROUNDS = int(limits.get("max_rounds", old_limits[0]))
        execution.MAX_TOOL_CALLS_PER_ROUND = int(
            limits.get("max_tool_calls", old_limits[1]))
        execution.MAX_OBS_CHARS = int(limits.get("max_obs_chars", old_limits[2]))
        utils.MAX_OBS_CHARS = execution.MAX_OBS_CHARS

        if record:
            from openai import OpenAI
            from config import (API_KEY, BASE_URL, MAX_API_RETRIES,
                                API_TIMEOUT, MODEL, THINKING)
            from tools import TOOLS_DESC
            from tracing import hash_text
            from eval.recordings import RecordClient
            if API_KEY == "sk-your-key-here":
                raise RuntimeError("--record needs DEEPSEEK_API_KEY")
            os.makedirs(os.path.dirname(rec_path), exist_ok=True)
            inner = OpenAI(api_key=API_KEY, base_url=BASE_URL,
                           max_retries=MAX_API_RETRIES,
                           timeout=API_TIMEOUT)
            meta = {
                "model": MODEL, "thinking": THINKING,
                "tools_hash": hash_text(json.dumps(
                    TOOLS_DESC, ensure_ascii=False, sort_keys=True)),
                "prompt_hash": hash_text(case["prompt"]),
            }
            client = RecordClient(inner, rec_path, case["id"], meta=meta)
        elif replay:
            from eval.recordings import ReplayClient
            client = ReplayClient(rec_path)
        else:
            client = FakeClient(case)

        tracer = (NullTracer() if os.environ.get("MINI_AGENT_TRACE") == "0"
                  else Tracer(trace_path))
        agent = ReActAgent(client=client, tracer=tracer)

        with redirect_stdout(StringIO()):
            result = agent.run(case["prompt"])

        # The agent converts model errors into strings; a missing/stale
        # recording is a harness error, not a case failure.
        if replay and getattr(client, "missing", None):
            raise RuntimeError(
                f"recording missing or stale for {len(client.missing)} request(s)")

        events = read_trace(trace_path)
        failures = check_case(agent, case, result, client.requests, events)
        tools = sum(len(m.get("tool_calls") or []) for m in agent.messages
                    if m.get("role") == "assistant")
        tokens = sum(e.get("total_tokens") or 0 for e in events
                     if e.get("event") == "model_request")
        return CaseResult(
            case_id=case["id"], passed=not failures, failures=failures,
            rounds=agent._rounds_used, tools=tools, tokens=tokens,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
            trace_path=trace_path)
    except Exception as e:
        return CaseResult(
            case_id=case.get("id", "<unknown>"), harness_error=True,
            failures=[f"{type(e).__name__}: {e}",
                      traceback.format_exc(limit=2)],
            duration_ms=round((time.monotonic() - started) * 1000, 1),
            trace_path=trace_path)
    finally:
        for name in added:
            if old_tools[name] is None:
                TOOLS.pop(name, None)
            else:
                TOOLS[name] = old_tools[name]
            if old_meta[name] is None:
                TOOL_META.pop(name, None)
            else:
                TOOL_META[name] = old_meta[name]
        (react_loop.MAX_ROUNDS, execution.MAX_TOOL_CALLS_PER_ROUND,
         execution.MAX_OBS_CHARS, utils.MAX_OBS_CHARS) = old_limits


def print_table(results):
    print(f"{'id':22} {'result':7} {'rounds':>6} {'tools':>5} "
          f"{'tokens':>7} {'ms':>7}")
    for result in results:
        status = ("ERROR" if result.harness_error
                  else "PASS" if result.passed else "FAIL")
        print(f"{result.case_id:22} {status:7} {result.rounds:>6} "
              f"{result.tools:>5} {result.tokens:>7} {result.duration_ms:>7.1f}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run offline eval cases.")
    parser.add_argument("--suite", help="smoke / failure / live")
    parser.add_argument("--case", help="one case id")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--record", action="store_true",
                        help="run against the real API and save responses")
    parser.add_argument("--replay", action="store_true",
                        help="replay a saved recording offline")
    args = parser.parse_args(argv)
    if args.record and args.replay:
        print("--record and --replay are mutually exclusive", file=sys.stderr)
        return 2

    try:
        cases = load_cases(args.suite, args.case)
    except Exception as e:
        print(f"harness error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if not cases:
        print("no cases matched", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="mini_agent_eval_") as trace_dir:
        results = [run_case(case, trace_dir, args.record, args.replay)
                   for case in cases]
        # Print while the trace directory still exists, so trace_path is usable.
        if args.json:
            print(json.dumps([result.__dict__ for result in results],
                             ensure_ascii=False, indent=2))
        else:
            print_table(results)
            for result in results:
                if result.failures:
                    print(f"\n{result.case_id}:")
                    for failure in result.failures:
                        print(f"  - {failure}")

    if any(result.harness_error for result in results):
        return 2
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
```
### 4.15 新增 `eval/recordings.py`

```python
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
```
### 4.16 新增 `eval/trace.py`

```python
"""Read, validate, and summarize trace JSONL files."""

import argparse
import json
import os
import re
import sys

REQUIRED = ("v", "ts", "trace_id", "span_id", "parent_span_id",
            "event", "actor")
SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|api[_-]?key\s*[:=]|"
                    r"authorization\s*[:=]|/home/[^\s\"]+)", re.I)


def load(path):
    events = []
    with open(path, encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{number}: invalid JSON: {e}") from e
    return events


def validate(events, allow_missing_parent=False, allow_open_calls=False):
    problems = []
    spans = set()
    for event in events:
        missing = [key for key in REQUIRED if key not in event]
        if missing:
            problems.append(f"event {event.get('event')!r} missing {missing}")
        if "span_id" in event:
            spans.add(event["span_id"])

    calls, results = {}, {}
    for event in events:
        parent = event.get("parent_span_id")
        if parent is not None and parent == event.get("span_id"):
            problems.append(
                f"{event.get('event')} span {event.get('span_id')} "
                f"is its own parent")
        if (parent is not None and parent not in spans
                and not allow_missing_parent):
            problems.append(
                f"{event.get('event')} span {event.get('span_id')} "
                f"has unknown parent {parent}")
        if event.get("event") == "tool_call":
            calls[event.get("tool_call_id")] = event
        if event.get("event") == "tool_result":
            results[event.get("tool_call_id")] = event

    if not allow_open_calls:
        if calls.keys() - results.keys():
            problems.append(f"tool_calls without results: {sorted(calls.keys() - results.keys())}")
        if results.keys() - calls.keys():
            problems.append(f"tool_results without calls: {sorted(results.keys() - calls.keys())}")
    for call_id in calls.keys() & results.keys():
        if calls[call_id].get("span_id") != results[call_id].get("span_id"):
            problems.append(f"tool pair {call_id} has different span ids")
    return problems


def secrets(events):
    found = []
    for event in events:
        text = json.dumps(event, ensure_ascii=False)
        match = SECRET.search(text)
        if match:
            found.append(f"{event.get('event')}: {match.group(0)[:40]}")
    return found


def summary(events):
    counts = {}
    total_in = total_out = 0
    tools = {}
    errors = 0
    for event in events:
        name = event.get("event")
        counts[name] = counts.get(name, 0) + 1
        if name == "model_request":
            total_in += event.get("input_tokens") or 0
            total_out += event.get("output_tokens") or 0
        if name == "tool_call":
            tool = event.get("tool")
            tools[tool] = tools.get(tool, 0) + 1
        if name == "error":
            errors += 1
    turns = len({event.get("turn") for event in events
                 if event.get("event") == "turn"
                 and event.get("status") == "start"})
    rounds = sum(1 for event in events
                 if event.get("event") == "round"
                 and event.get("status") == "start")
    return {
        "events": len(events),
        "turns": turns,
        "rounds": rounds,
        "model_requests": counts.get("model_request", 0),
        "input_tokens": total_in,
        "output_tokens": total_out,
        "tools": tools,
        "errors": errors,
        "counts": counts,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate a trace JSONL file.")
    parser.add_argument("--file", required=True, nargs="+")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--allow-missing-parent", action="store_true")
    parser.add_argument("--allow-open-calls", action="store_true")
    args = parser.parse_args(argv)

    failed = False
    for path in args.file:
        try:
            events = load(path)
        except Exception as e:
            print(f"{path}: {e}", file=sys.stderr)
            failed = True
            continue
        problems = validate(events, args.allow_missing_parent,
                            args.allow_open_calls)
        leaks = secrets(events)
        if args.check:
            print(f"{path}: {len(events)} events, "
                  f"{len(problems)} problems, {len(leaks)} secret hits")
            for problem in problems[:20]:
                print(f"  - {problem}")
            for leak in leaks[:20]:
                print(f"  - secret: {leak}")
            failed = failed or bool(problems or leaks)
        if args.summary:
            print(json.dumps(summary(events), ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
```
### 4.17 `eval/cases/*.json`

10 个 case：6 个 `smoke`，4 个 `failure`。

| id | suite | 场景 | 关键断言 |
| --- | --- | --- | --- |
| `simple-final` | smoke | 直接回答 | 0 工具、1 轮、final 含 `4` |
| `single-tool-read` | smoke | 读文件再回答 | 工具序列 `read_file`、参数 `path=a.txt` |
| `two-tool-order` | smoke | 读 + 写 | 工具顺序、history 合法、无工具错误 |
| `parallel-safe` | smoke | 两个 safe 工具共享 `Barrier` | 真的重叠、2 个调用、无错误 |
| `plan-two-steps` | smoke | planner 返回 2 步 | 4 个 `plan_step` 事件、最终答案 |
| `schema-contract` | smoke | 请求契约 | system prompt 含 `Final Answer:`、schema 含 `read_file` / `write_file` |
| `bad-tool-json` | failure | 参数是坏 JSON | observation 含 `Invalid JSON`、循环继续 |
| `tool-raises` | failure | 工具抛异常 | observation 含 `boom`、循环继续 |
| `planner-bad-json` | failure | planner 输出非 JSON | 回退 ReAct、final 含 `fallback` |
| `max-rounds` | failure | 模型一直发 tool_call | 返回 `Max steps reached`、1 个 `error` 事件 |

一个完整 case（`single-tool-read.json`）：

```json
{
  "schema_version": 1,
  "id": "single-tool-read",
  "suite": "smoke",
  "prompt": "Read a.txt and answer.",
  "plan": {
    "needs_plan": false,
    "steps": []
  },
  "script": [
    {
      "type": "tool_call",
      "name": "read_file",
      "arguments": {
        "path": "a.txt"
      },
      "id": "call_read"
    },
    {
      "type": "final",
      "content": "Final Answer: hello"
    }
  ],
  "tools": {
    "read_file": {
      "result": "hello",
      "parallel_safe": true
    }
  },
  "limits": {
    "max_rounds": 3
  },
  "expect": {
    "history_valid": true,
    "tool_sequence": [
      "read_file"
    ],
    "tool_args": [
      {
        "path": "a.txt"
      }
    ],
    "final_contains": [
      "hello"
    ],
    "max_rounds": 2,
    "max_model_requests": 3,
    "request": {
      "system_prompt_contains": [
        "Final Answer:"
      ],
      "tool_schema_names": [
        "read_file"
      ]
    }
  }
}
```
另一个（`bad-tool-json.json`，失败路径）：

```json
{
  "schema_version": 1,
  "id": "bad-tool-json",
  "suite": "failure",
  "prompt": "Read a.txt even if the arguments are bad.",
  "plan": {
    "needs_plan": false,
    "steps": []
  },
  "script": [
    {
      "type": "tool_call",
      "name": "read_file",
      "arguments_raw": "{bad",
      "id": "call_bad"
    },
    {
      "type": "final",
      "content": "Final Answer: recovered"
    }
  ],
  "tools": {
    "read_file": {
      "result": "hello",
      "parallel_safe": true
    }
  },
  "limits": {
    "max_rounds": 3
  },
  "expect": {
    "history_valid": true,
    "tool_sequence": [
      "read_file"
    ],
    "tool_results_contain": [
      "Invalid JSON"
    ],
    "final_contains": [
      "recovered"
    ],
    "max_rounds": 2,
    "max_model_requests": 3
  }
}
```
### 4.18 新增 `tests/test_tracing.py`

```python
"""Tracer schema, concurrency, redaction, rotation, and failure containment."""

import contextlib
import io
import json
import os
import tempfile
import threading

from tracing import Tracer, NullTracer, hash_text, redact_args

REQUIRED = ("v", "ts", "trace_id", "span_id", "parent_span_id",
            "event", "actor")


def _lines(path):
    with open(path, encoding="utf-8") as f:
        return [line for line in f if line.strip()]


with tempfile.TemporaryDirectory() as tmp:
    # 11a. Disabled tracer is a true no-op.
    null = NullTracer()
    assert null.new_span() is None
    null.emit("turn", actor="test", status="start")
    assert not os.listdir(tmp)

    # 11b. One event is one parseable JSON line with the common schema.
    path = os.path.join(tmp, "one.jsonl")
    tracer = Tracer(path)
    tracer.emit("turn", actor="test", turn=1, status="start",
                prompt_hash=hash_text("hello"))
    event = json.loads(_lines(path)[0])
    assert all(key in event for key in REQUIRED), event
    assert event["event"] == "turn" and event["actor"] == "test"
    assert event["trace_id"] and event["span_id"]

    # 11c. Concurrent writers produce whole lines and unique spans.
    concurrent = os.path.join(tmp, "concurrent.jsonl")
    tracer = Tracer(concurrent)
    barrier = threading.Barrier(4, timeout=2)

    def worker(number):
        barrier.wait()
        for index in range(50):
            tracer.emit("tool_call", actor="test",
                        span_id=tracer.new_span(),
                        tool_call_id=f"{number}-{index}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    events = [json.loads(line) for line in _lines(concurrent)]
    assert len(events) == 200, len(events)
    assert len({event["span_id"] for event in events}) == 200

    # 11d. Credential-shaped keys are redacted; usage counters are not.
    preview = redact_args({
        "api_key": "sk-1234567890",
        "authorization": "Bearer x",
        "path": "/home/bruce/project/a.txt",
        "total_tokens": 13,
        "nested": {"password": "p", "secret": "s"},
    })
    assert "sk-1234567890" not in preview, preview
    assert "/home/bruce" not in preview, preview
    assert preview.count("<REDACTED>") >= 4, preview
    assert "13" in preview, preview

    # 11e. Rotation keeps whole lines; no line is split across files.
    rotated = os.path.join(tmp, "rotated.jsonl")
    tracer = Tracer(rotated, max_bytes=250, keep=50)
    for index in range(20):
        tracer.emit("turn", actor="test", turn=index, status="start",
                    span_id=f"{index:016x}")
    files = [rotated] + [f"{rotated}.{i}" for i in range(1, 51)
                         if os.path.exists(f"{rotated}.{i}")]
    total = 0
    for filename in files:
        for line in _lines(filename):
            json.loads(line)
            total += 1
    assert total == 20, total
    assert os.path.exists(rotated + ".1"), "rotation did not happen"

    # 11f. An unwritable path disables tracing instead of raising.
    with tempfile.NamedTemporaryFile() as blocker:
        with contextlib.redirect_stderr(io.StringIO()):
            broken = Tracer(os.path.join(blocker.name, "trace.jsonl"))
        assert broken.enabled is False
        broken.emit("turn", actor="test", status="start")

print("11. Tracer: no-op / schema / concurrency / redaction / rotation / failure")
```
### 4.19 新增 `tests/test_eval_harness.py`

```python
"""Eval runner, fake-model routing, failure exit codes, and trace links."""

import contextlib
import copy
import io
import json
import os
import tempfile

from eval import runner
from eval.fake_model import FakeClient
from eval.recordings import RecordClient, ReplayClient, request_hash
from eval.trace import load, validate


def quiet(function, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        return function(*args, **kwargs)


# 12a. A passing case exits 0 and produces a valid trace.
assert quiet(runner.main, ["--case", "simple-final"]) == 0
case = runner.load_cases(case_id="simple-final")[0]
with tempfile.TemporaryDirectory() as trace_dir:
    result = runner.run_case(case, trace_dir)
    assert result.passed, result.failures
    events = load(os.path.join(trace_dir, "simple-final.jsonl"))
    assert validate(events) == [], validate(events)
    assert any(event["event"] == "tool_result" for event in events) is False
    assert any(event["event"] == "model_request" for event in events)

# 12b. A broken expectation exits non-zero, and it is a case failure (1),
# not a harness error (2).
broken = copy.deepcopy(case)
broken["expect"]["final_contains"] = ["no-such-text"]
with tempfile.TemporaryDirectory() as trace_dir:
    result = runner.run_case(broken, trace_dir)
    assert not result.passed and not result.harness_error, result
assert quiet(runner.main, ["--suite", "does-not-exist"]) == 2

# 12c. FakeClient routes planner / executor / summarizer correctly.
planner = FakeClient(case)
planner_chunks = list(planner.create(
    model="m", messages=[{"role": "system", "content": "plan"}],
    stream=True))
assert len(planner_chunks) == 2
assert "needs_plan" in planner_chunks[0].choices[0].delta.content

executor = FakeClient(case)
executor_chunks = list(executor.create(
    model="m", messages=[{"role": "system", "content": "exec"}],
    tools=[], stream=True))
assert any(chunk.choices and chunk.choices[0].delta.content == "Final Answer: 4"
           for chunk in executor_chunks)

summarizer = FakeClient(case)
response = summarizer.create(model="m", messages=[], stream=False)
assert response.choices[0].message.content

# 12d. A tool case produces matched tool_call / tool_result spans.
tool_case = runner.load_cases(case_id="single-tool-read")[0]
with tempfile.TemporaryDirectory() as trace_dir:
    result = runner.run_case(tool_case, trace_dir)
    assert result.passed, result.failures
    events = load(os.path.join(trace_dir, "single-tool-read.jsonl"))
    assert validate(events) == [], validate(events)
    calls = [e for e in events if e["event"] == "tool_call"]
    results = [e for e in events if e["event"] == "tool_result"]
    assert len(calls) == 1 and len(results) == 1
    assert calls[0]["span_id"] == results[0]["span_id"]
    assert calls[0]["tool_call_id"] == results[0]["tool_call_id"]

# 12e. Recording keys are stable and stale requests fail loudly.
base = {"model": "m", "messages": [{"role": "user", "content": "x"}],
        "tools": [], "stream": True}
assert request_hash(base) == request_hash(dict(base))
changed = {"model": "m", "messages": [{"role": "user", "content": "y"}],
           "tools": [], "stream": True}
assert request_hash(base) != request_hash(changed)
with tempfile.TemporaryDirectory() as trace_dir:
    replay = ReplayClient(os.path.join(trace_dir, "missing.jsonl"))
    try:
        replay.create(**base)
    except RuntimeError:
        pass
    else:
        raise AssertionError("missing recording did not fail")

# 12f. Record then replay a streamed response without any network.
with tempfile.TemporaryDirectory() as trace_dir:
    recording = os.path.join(trace_dir, "recording.jsonl")
    recorder = RecordClient(FakeClient(case), recording, case["id"])
    original = list(recorder.create(
        model="m", messages=[{"role": "user", "content": "x"}],
        tools=[], stream=True))
    assert os.path.exists(recording)
    replay = ReplayClient(recording)
    replayed = list(replay.create(
        model="m", messages=[{"role": "user", "content": "x"}],
        tools=[], stream=True))
    assert any(chunk.choices and chunk.choices[0].delta.content == "Final Answer: 4"
               for chunk in replayed)

print("12. Eval harness: pass/fail/error / routing / trace pairing / record-replay")
```
### 4.20 修改 `_test.py`

```diff
--- a/_test.py
+++ b/_test.py
@@ -6,6 +6,7 @@
 from tests import (
     test_tools, test_context, test_presentation, test_responses,
     test_memory, test_cli, test_planning, test_parallel,
+    test_tracing, test_eval_harness,
 )
 
 print("\nAll offline assertion groups passed.")
```
### 4.21 修改 `README.md` / `CLAUDE.md`

- `README.md`：功能列表加 trace/eval；布局加 `tracing.py`、`eval/`；`Verify` 加两个 eval suite；新增 `Eval and tracing`、`Differences from 09`、`Limitations`；文档链接从缺失的 `AGENTS.md` 改为 `CLAUDE.md` 和两篇 eval/tracing 文档。
- `CLAUDE.md`：Status 改为“lesson 10 eval & tracing implemented”；Running 的 cwd 改为本目录并加 eval 命令；测试从 8 个模块改为 10 个模块；日志 gate 从 19 改为 21；新增本课特性、线程模型、config-by-value、验收记录；旧的“parallel not started”整段替换。

---

## 5. 测试与验证

### 5.1 测试矩阵

| 层 | 命令 | 覆盖 | 结果 |
| --- | --- | --- | --- |
| 语法 | `python3 -m compileall -q .` | 全部 `.py` | exit 0 |
| 既有单测 | `python3 _test.py` | 10 个测试模块，含 tracer / eval harness | exit 0 |
| smoke eval | `python3 eval/runner.py --suite smoke` | 6 个成功路径 | 6/6 PASS |
| failure eval | `python3 eval/runner.py --suite failure` | 4 个失败路径 | 4/4 PASS |
| trace 校验 | `python3 eval/trace.py --file ... --check --summary` | schema、父子链、tool 配对、秘密扫描 | 17 events，0 problems，0 secret hits |
| 开关 | `MINI_AGENT_TRACE=0/1 python3 -c ...` | 默认关闭 / 显式开启 | False / True |
| 录制 / 回放 | `tests/test_eval_harness.py` 12e/12f；`eval/runner.py --replay` | 请求 hash 稳定、改 prompt 失效、缺失录制 exit 2、录制→回放内容一致 | PASS |

### 5.2 关键测试断言

- **tracer 并发**：4 线程 × 50 次 `emit`，`Barrier` 同时起跑；200 行全部可解析，200 个 `span_id` 唯一。
- **轮转**：`max_bytes=250` 写 20 条；所有行仍可解析，`.1` 出现，没有半行。
- **脱敏**：`api_key` / `authorization` / `password` / `secret` 变成 `<REDACTED>`；`/home/bruce/...` 变成 `~/...`；**`total_tokens=13` 必须保留**（不能把 usage 计数器当秘密）。
- **失败降级**：路径不可写时 `Tracer.enabled=False`，`emit` 不抛。
- **并行**：两个 safe fake 工具共享 `Barrier(2)`；串行会让 barrier 超时并产生 `Error:`，case 失败。
- **协议**：每个 case 都跑 `validate_history(agent.messages)`；`tool_call` / `tool_result` 的 `span_id` 与 `tool_call_id` 必须成对。
- **请求契约**：`schema-contract` 检查 system prompt 含 `Final Answer:`、tools schema 含 `read_file` / `write_file`。
- **失败路径**：坏 JSON、工具异常、planner 坏 JSON、max rounds 各一个 case。

### 5.3 验收清单

- [x] `python3 _test.py` exit 0（10 组）
- [x] `python3 -m compileall -q .` exit 0
- [x] `python3 main.py --help` exit 0，含 `MINI_AGENT_TRACE`
- [x] `python3 eval/runner.py --suite smoke` 6/6 PASS
- [x] `python3 eval/runner.py --suite failure` 4/4 PASS
- [x] trace 校验 0 problems / 0 secret hits
- [x] `MINI_AGENT_TRACE=0` 不产生 trace；`=1` 产生
- [x] 默认不写 prompt / 工具结果；`args_preview` 脱敏
- [x] `tool_call` / `tool_result` 成对，N 个调用 → N 个结果
- [x] 失败调用也产生 `tool_result`
- [x] 请求 hash 改变时 replay 失败，不静默通过
- [x] README / CLAUDE 已更新

### 5.4 未执行

- live `--record` / `--replay`：代码已实现；离线 fake 录制→回放、缺失录制 exit 2 已测；本轮没有发起真实 API 请求。
- OTel / collector / 监控后端：本课不做。
- 多 agent trace tree：15 才做；`agent_id` 字段预留。

---

## 6. 风险、限制与回滚

| 风险 | 缓解 |
| --- | --- |
| trace 体积 | `max_bytes` + `keep` 轮转；只写事件，不写历史 |
| 秘密泄露 | 默认不写内容；凭据 key 脱敏；`0600`；`eval/trace.py` 扫描 |
| 日志截断 | trace 写独立文件，不复用 `EXPORT_LOG_PATH` |
| 录制过期 | 请求 hash + 缺失即 `RuntimeError`（exit 2） |
| fake model 保真度 | 请求契约断言 + 手动 live smoke |
| config-by-value | import 前设 env；per-case patch 消费模块全局 |
| Ctrl-C | 每行独立写；不捕获 `BaseException`；最多丢最后一行 |
| 无 git | 改前 `cp` 备份；按 §0.5 分阶段应用 |

回滚：

- 只要 `MINI_AGENT_TRACE=0`，trace 就完全 no-op；不需要删代码。
- 删 `eval/` 不影响生产；`agent/` 不 import `eval/`。
- 修改文件可从 `/tmp/eval_impl_backup_20261026_162306/` 恢复（本次环境）。

---

## 7. 参考资料

- 本目录 `doc/EVAL_AND_TRACING_DESIGN.md`：设计、业界调研、判据、测试方案。
- `../ROADMAP.md` §4「10 — Eval & Tracing」。
- `doc/PARALLEL_TOOL_EXECUTION_DESIGN.md` / `_IMPLEMENTATION.md`：本仓库前一篇实现记录的格式基准。
- 外部资料见设计文档 §10（Claude Code / Codex CLI 官方文档与源码，访问 2026-10-26）。
