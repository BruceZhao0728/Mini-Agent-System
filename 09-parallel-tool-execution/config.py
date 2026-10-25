'''Application configuration grouped by service, logging, runtime, and feature.'''

import time
import os

# Timestamp used to build a unique log file path when this module is imported.
current_time = time.localtime()
current_time_str = time.strftime("%Y%m%d_%H%M%S", current_time)

# API endpoint and model selection. Environment variables override these defaults.
API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-your-key-here")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.getenv("DEEPSEEK_MODEL_NAME", "deepseek-flash")
THINKING = os.getenv("DEEPSEEK_THINKING", "enabled")  # "enabled" or "disabled" — DeepSeek thinking mode is on by default; set to "disabled" to compare against a plain chat model

# Request logging controls and per-event gates.
EXPORT_LOG = True  # Set to False to disable logging of API requests and responses
EXPORT_LOG_CHOICES = {
	"initial-messages": False,
	"message-beginning": True,
	"llm-response": False,
	"llm-response-message": True,
	"llm-response-message-content": True,
	"messages-after-tool-execution": True,
	"final-answer": True,
	"thought": True,
	"observation-clipped": True,
	"tool-retry": True,
	"round-tool-call-cap": True,
	"arguments-size": True,
	"turn": True,
	"llm-usage": True,		# The raw usage of every API call — the only source of the context number
	"llm-finish-reason": True,	# Why each generation ended (stop / length / …) and whether the reply looked complete
	"context": True,		# Occupancy and history size per round (the sampling point of COMPACT.md §7.1)
	"compact": True,		# Compaction events: cut point, counts/chars before and after, the summary text
	"memory": True,			# The memory index spliced into the system prompt at session start (MEMORY.md §5.4)
	"parallel": True,		# Per-round batch shape, wall time, sum(call time)
	"tool-duration": True,	# Per-call duration; proves overlap
    "planning": True,
}
EXPORT_LOG_DIR = "logs"  # Directory to store log files
EXPORT_LOG_NAME_TEMPLATE = "deepseek_log_{current_time_str}.log"  # Filename format for each run log
EXPORT_LOG_PATH = os.path.join(
    EXPORT_LOG_DIR,
    EXPORT_LOG_NAME_TEMPLATE.format(current_time_str=current_time_str)
)

# Terminal presentation. This affects display only, never stored history.
RENDER_MARKDOWN = True  # Render the model's markdown as terminal styling (bold / italic / quote…); only takes effect on a tty — history and logs always keep the raw markdown

# API and tool execution limits.
MAX_API_RETRIES = 3  # SDK retries for retryable API failures
API_TIMEOUT = 60	# Timeout for API requests in seconds

MAX_ROUNDS = 100  # Maximum model/tool rounds for one ReAct step
MAX_OBS_CHARS = 8000				# Maximum number of characters from tool output to be included in history per round
MAX_TOOL_CALLS_PER_ROUND = 20		# Maximum number of tool calls allowed per round

MAX_TOOL_RETRIES = 3
TOOL_RETRY_BACKOFF = 0.5			# 0.5 seconds of exponential backoff for tool retries
MAX_TOOL_RETRY_BACKOFF_TIME = 10		# Maximum backoff time for tool retries

# Parallel tool execution. Only "0" disables it; any other value enables it.
PARALLEL_ENABLED = os.getenv("PARALLEL_ENABLED", "1") != "0"
PARALLEL_MAX_WORKERS = 4   # configured cap per safe group; clamped to >= 1

# ---------------------------------------------------------------------------
# Context budget and compaction (see COMPACT.md). The window comes from the
# environment, so `DEEPSEEK_CONTEXT_WINDOW=4000 python3 main.py` pushes the
# thresholds down to something testable without editing this file.
# ---------------------------------------------------------------------------
# The official spec of deepseek-v4.1-flash: context 1,000,000, max output
# 393,216 (≈384K). What goes here is **how much you are willing to spend in
# this session**, not the model's ceiling — setting DEEPSEEK_CONTEXT_WINDOW
# smaller (200000, say) is legal and common: a share only means something
# against a working budget, and 70% of 1M is 700K, which is already an
# expensive request. The output limit is unrelated to this file: the main loop
# sets no max_tokens — only the summarization call sets COMPACT_SUMMARY_MAX_TOKENS.
CONTEXT_WINDOW_TOKENS = int(os.getenv("DEEPSEEK_CONTEXT_WINDOW", "784000"))	# The context window you are willing to spend in this session (tokens)
CONTEXT_WARN_RATIO = 0.70			# Above this → remind the user to run /compact
CONTEXT_DANGER_RATIO = 0.85			# Above this → strong reminder
REQUEST_USAGE = True				# Send stream_options={"include_usage": True} on streaming requests; turn it off if the endpoint rejects the parameter

COMPACT_KEEP_TURNS = 2				# Keep the last N whole turns verbatim after a compaction
COMPACT_KEEP_GROUPS = 6				# Fallback for a single long turn: keep the last N groups (a group = assistant(tool_calls) + all of its results)
COMPACT_SUMMARY_MAX_TOKENS = 1200		# Output cap of the summarization call
COMPACT_SUMMARY_CHUNK_CHARS = 24000		# Fold the range through several calls once it exceeds this many characters
COMPACT_TOOL_RESULT_CHARS = 1200		# Per-result truncation when writing the summarizer's input (the main conversation uses MAX_OBS_CHARS)
COMPACT_REASONING_CHARS = 0			# Reasoning stays out of the summary input (0 = never written)

# Prompts sent to the executor and the context summarizer.
DEFAULT_SYSTEM_PROMPT = """You are a ReAct agent. Follow this strict format:

Thought: [Analyze the current state and decide what to do next]
Action: [Call a tool]
Observation: [Tool result]

Repeat Thought -> Action -> Observation until the task is complete.
End with: Final Answer: [answer]

Long-term memory: If this prompt contains a "Long-term Memory" section, it is an index of notes from earlier sessions.
- Use recall_notes when details are missing from the index or relevance is unclear; do not guess from memory.
- Use write_note to retain facts useful across sessions, especially facts absent from the code, Git history, and project documentation.
- The [n] at the start of each index entry is its note ID. Use forget_note to remove an incorrect or obsolete note instead of adding a conflicting correction.
- Notes are snapshots from when they were written and may be stale; verify them before acting."""

# The summarizer's prompt. Change it together with the code that consumes its
# output (_summarize / compact) — the same relationship DEFAULT_SYSTEM_PROMPT
# has with the literals in run(). It is an f-string so the over-cap example
# below is the message the agent actually writes, whatever the budget is; a
# literal { in the text would have to be escaped.
COMPACT_SUMMARY_PROMPT = f"""You are compressing the history of an agent conversation so it fits a smaller context window. What you receive is a transcript of earlier work; the most recent turns are kept verbatim and are not shown to you.

Write a summary that lets the agent continue the work without the original messages. Cover, in this order:

1. The user's original request, reproduced as literally as you can.
2. Anything else the user asked for along the way that is still relevant.
3. Key decisions and why they were made.
4. Files, paths and commands that matter, with their concrete findings.
5. What is already done.
6. What is still pending, and the next concrete step.

Rules:
- Only state what the transcript supports. Never invent a file, path or result.
- Keep exact identifiers (paths, function names, numbers) verbatim — do not paraphrase them.
- Some tool results say things like "Error: The maximum number of tool calls per round is {MAX_TOOL_CALLS_PER_ROUND}"; those calls were never executed. Do not record them as findings.
- If the transcript already contains a summary of even earlier work, treat it as established fact and carry its still-relevant content forward.
- Be concise: this summary replaces the transcript, so it must be much shorter than it."""

# ---------------------------------------------------------------------------
# Long-term memory (see MEMORY.md). The index is spliced into messages[0], so
# these two numbers are a *token* budget, not a display preference: they are
# paid for on every request of the session. memory.py carries the same values
# as defaults so it can be used standalone; the agent passes these.
# ---------------------------------------------------------------------------
MEMORY_INDEX_MAX_NOTES = 40			# Most notes the index may list; beyond this it says how many are hidden
MEMORY_INDEX_MAX_CHARS = 4000			# Hard character cap on the whole index block (~1K tokens)
MEMORY_INDEX_HEADER = "\n\nLong-term Memory (notes from earlier sessions, in chronological order; this is an index only, use recall_notes for details):\n"

# Planning prompt limits and instructions.
PLAN_MAX_STEPS = 8  # Reject plans that exceed this number of steps
PLAN_MAX_TOKENS = 800  # Maximum generated tokens for the planner response

PLAN_PROMPT = """You are the planning stage of a tool-using assistant.
Decide whether the user's request needs multiple distinct execution steps.
For a simple question or one direct action, return needs_plan=false and an empty steps list.
For a task with multiple goals, dependencies, or information-gathering stages, return
needs_plan=true and an ordered list of short, concrete steps. Each step should describe
one outcome the executor can work toward using the available conversation and tools.
Do not claim that any step has already been executed. Do not include tool-call JSON.

Return exactly one JSON object, with this shape and no markdown fences or extra text:
{"needs_plan": false, "steps": []}

When a plan is needed, use this shape:
{"needs_plan": true, "steps": ["First step", "Second step"]}
"""
