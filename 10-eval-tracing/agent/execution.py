"""Tool execution, retry handling, and streamed response assembly."""

import json
import time
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from config import (
    EXPORT_LOG, EXPORT_LOG_CHOICES, EXPORT_LOG_PATH, MAX_TOOL_RETRIES,
    TOOL_RETRY_BACKOFF, MAX_TOOL_RETRY_BACKOFF_TIME, MAX_OBS_CHARS,
    MAX_TOOL_CALLS_PER_ROUND, PARALLEL_ENABLED, PARALLEL_MAX_WORKERS,
)
from tools import TOOLS, TOOL_META
from utils import clip
from presentation import MarkdownStream, rendering_enabled
import tracing

RETRIABLE_ERRORS = (subprocess.TimeoutExpired, ConnectionError, TimeoutError)

# Every write reachable from a worker takes this lock. The main-thread log
# writes (arguments-size / observation-clipped / parallel / tool-duration) run
# while no worker is executing, so they intentionally do not take it.
_LOG_LOCK = threading.Lock()

class ExecutionMixin:
	"""Group related behavior for tool."""
	def _parse_args(self, tc) -> tuple[dict, str]:
		'''
		Parse the arguments of a tool call.

		Args:
			tc: The tool call object containing the function name and arguments.

		Returns:
			tuple: A tuple containing the parsed arguments as a dictionary and an error message.
				If the parsing is successful, the error message will be an empty string.
				Otherwise, the error message will describe the issue encountered during parsing.
		'''
		try:
			args = json.loads(tc.function.arguments or "{}")
		except json.JSONDecodeError as e:
			return {}, (f"Error: Invalid JSON in arguments ({e}). "
						f"Please provide valid JSON arguments.")
		if not isinstance(args, dict):
			return {}, (f"Error: Arguments must be a JSON object, but received {type(args).__name__}. "
						f"Please provide valid JSON arguments.")
		return args, ""

	def _call_tool(self, action_name: str, action_args: dict,
	               tool_call_id: str | None = None) -> str:
		'''
		Execute a single tool call and return the observation.

		Args:
			action_name (str): The name of the tool to be called.
			action_args (dict): The arguments to be passed to the tool.
			tool_call_id (str | None): The originating call id, used in retry logs.

		Returns:
			str: The observation returned by the tool, or an error message if the tool call fails.
		'''
		attempt = 0
		while True:
			try:
				observation = TOOLS[action_name](**action_args)
				return str(observation)  # Ensure the observation is a string
			except RETRIABLE_ERRORS as e:
				# For retriable errors, we will retry the tool call with exponential backoff.
				attempt += 1
				if attempt > MAX_TOOL_RETRIES:
					# If exceeded max retries, return an error message.
					return f"Error: {action_name} failed after {MAX_TOOL_RETRIES} retries due to {type(e).__name__}: {e}"
				
				if EXPORT_LOG and EXPORT_LOG_CHOICES["tool-retry"]:
					with _LOG_LOCK:
						with open(EXPORT_LOG_PATH, "a") as f:
							f.write(f"Tool retry {attempt}/{MAX_TOOL_RETRIES}: "
							        f"id={tool_call_id or '-'} {action_name} — "
							        f"{type(e).__name__}: {e}\n")
				
				# Exponential backoff with an upper limit to avoid excessively long waits
				time.sleep(min(TOOL_RETRY_BACKOFF * 2 ** (attempt - 1), MAX_TOOL_RETRY_BACKOFF_TIME))  
			except Exception as e:
				return f"Error: {action_name} failed due to {type(e).__name__}: {e}"

	def _parallel_safe(self, name: str) -> bool:
		'''Metadata is the only source; unknown names fail closed.'''
		return TOOL_META.get(name, {}).get("parallel_safe") is True

	def _run_inline(self, plan: dict) -> tuple:
		'''Caller-thread execution: KeyboardInterrupt / SystemExit propagate.'''
		started = time.monotonic()
		observation = self._call_tool(
			plan["name"], plan["args"], tool_call_id=plan["tc"].id)
		return observation, time.monotonic() - started

	def _run_worker(self, plan: dict) -> tuple:
		'''Pool execution: every BaseException becomes an observation.'''
		started = time.monotonic()
		try:
			observation = self._call_tool(
				plan["name"], plan["args"], tool_call_id=plan["tc"].id)
		except BaseException as e:
			observation = (f"Error: {plan['name']} failed due to "
			               f"{type(e).__name__}: {e}")
		return observation, time.monotonic() - started

	def _execute_tool_calls(self, tool_calls: list, parallel=None) -> list:
		'''Execute one round's calls; return tool messages in tool_calls order.

		Phase 1 (caller thread): cap, parse, arguments-size, Action, classify.
		Phase 2: each *consecutive* run of safe calls goes through a pool; every
		unsafe or rejected call is a one-element group on the caller thread.
		Phase 3 (caller thread): clip/log/print in original order; return the
		messages. History is extended by the caller only after this succeeds.
		'''
		if parallel is None:
			parallel = PARALLEL_ENABLED
		n = len(tool_calls)

		if (EXPORT_LOG and EXPORT_LOG_CHOICES["round-tool-call-cap"]
				and n > MAX_TOOL_CALLS_PER_ROUND):
			with open(EXPORT_LOG_PATH, "a") as f:
				f.write(f"Round cap: {n} tool calls requested, "
				        f"executing first {MAX_TOOL_CALLS_PER_ROUND}\n")

		# Phase 1: admission on the caller thread.
		plans = []
		for i, tc in enumerate(tool_calls):
			name = tc.function.name
			if EXPORT_LOG and EXPORT_LOG_CHOICES["arguments-size"]:
				with open(EXPORT_LOG_PATH, "a") as f:
					f.write(f"Arguments size: [{i + 1}/{n}] {name} "
					        f"{len(tc.function.arguments or '')} chars\n")
			if i >= MAX_TOOL_CALLS_PER_ROUND:
				error = (f"Error: The maximum number of tool calls per round is "
				         f"{MAX_TOOL_CALLS_PER_ROUND}, this call was not executed. "
				         f"Please replan and initiate this call in the next round.")
				plans.append({"i": i, "tc": tc, "name": name,
				              "args": None, "error": error, "safe": False})
				continue
			args, error = self._parse_args(tc)
			if not error:
				label = f"[{i + 1}/{n}] " if n > 1 else ""
				print(f"🔧 Action: {label}{name}({args})")
			safe = bool(not error and parallel and self._parallel_safe(name))
			plans.append({"i": i, "tc": tc, "name": name, "args": args,
			              "error": error, "safe": safe})

		# Phase 1b: one trace span per call, emitted on the caller thread.
		for plan in plans:
			plan["span_id"] = self.tracer.new_span(self._round_span)
			raw_args = plan["tc"].function.arguments or ""
			self.tracer.emit(
				"tool_call", actor=f"tool:{plan['name']}",
				span_id=plan["span_id"],
				parent_span_id=self._round_span, turn=self.turn,
				plan_step=self._plan_step, round=self._round,
				round_global=self._rounds_used,
				tool=plan["name"], tool_call_id=plan["tc"].id,
				call_index=plan["i"] + 1, args_chars=len(raw_args),
				args_hash=tracing.hash_text(raw_args),
				args_preview=tracing.redact_args(plan["args"] or {}),
				parallel_safe=self._parallel_safe(plan["name"]),
				scheduled_parallel=plan["safe"],
				status="rejected" if plan["error"] else "requested",
				error_type="Rejected" if plan["error"] else None)

		# Phase 2: contiguous safe runs; error/unsafe are one-element barriers.
		groups, run = [], []
		for plan in plans:
			if plan["error"] or not plan["safe"]:
				if run:
					groups.append(run)
					run = []
				groups.append([plan])
			else:
				run.append(plan)
		if run:
			groups.append(run)

		phase2_started = time.monotonic()
		outcomes, shape = {}, []
		for group in groups:
			if len(group) == 1:
				plan = group[0]
				outcomes[plan["i"]] = ((plan["error"], 0.0) if plan["error"]
				                       else self._run_inline(plan))
				shape.append(
					f"rejected:{plan['name']}" if plan["error"]
					else f"{'inline' if plan['safe'] else 'serial'}:{plan['name']}")
				continue
			workers = max(1, min(int(PARALLEL_MAX_WORKERS), len(group)))
			shape.append(f"safe x{len(group)}")
			executor = ThreadPoolExecutor(max_workers=workers,
			                              thread_name_prefix="tool")
			try:
				futures = [(p["i"], executor.submit(self._run_worker, p))
				           for p in group]
				for i, future in futures:
					outcomes[i] = future.result()
			except BaseException:
				# Ctrl-C: cancel queued calls; do not wait for running ones.
				executor.shutdown(wait=False, cancel_futures=True)
				raise
			else:
				executor.shutdown(wait=True)
		wall = time.monotonic() - phase2_started

		if EXPORT_LOG and EXPORT_LOG_CHOICES["parallel"]:
			with open(EXPORT_LOG_PATH, "a") as f:
				f.write(f"Parallel: enabled={parallel} calls={n} "
				        f"groups=[{', '.join(shape)}] max_workers={PARALLEL_MAX_WORKERS} "
				        f"wall={wall:.3f}s sum_call_time="
				        f"{sum(d for _, d in outcomes.values()):.3f}s\n")

		# Phase 3: original order; rejected calls are observations like any other.
		messages = []
		for k, plan in enumerate(plans, 1):
			observation, duration = outcomes[plan["i"]]
			observation = str(observation)
			raw_len = len(observation)
			observation = clip(observation)
			if EXPORT_LOG and EXPORT_LOG_CHOICES["observation-clipped"]:
				with open(EXPORT_LOG_PATH, "a") as f:
					f.write(f"Observation size: [{k}/{n}] {plan['name']} "
					        f"{raw_len} chars"
					        f"{f' -> clipped to {len(observation)}' if raw_len > MAX_OBS_CHARS else ''}\n")
			if (EXPORT_LOG and EXPORT_LOG_CHOICES["tool-duration"]
					and not plan["error"]):
				with open(EXPORT_LOG_PATH, "a") as f:
					f.write(f"Tool duration: [{k}/{n}] {plan['name']} "
					        f"id={plan['tc'].id} {duration:.2f}s\n")
			label = f"[{k}/{n}] " if n > 1 else ""
			print(f"👁 Observation: {label}{observation[:200]}"
			      f"{'...' if len(observation) > 200 else ''}")
			self.tracer.emit(
				"tool_result", actor=f"tool:{plan['name']}",
				span_id=plan["span_id"],
				parent_span_id=self._round_span, turn=self.turn,
				plan_step=self._plan_step, round=self._round,
				round_global=self._rounds_used,
				tool=plan["name"], tool_call_id=plan["tc"].id,
				call_index=k, duration_ms=round(duration * 1000, 1),
				raw_chars=raw_len, result_chars=len(observation),
				clipped=raw_len > len(observation),
				status="error" if observation.startswith("Error:") else "ok")
			messages.append({"role": "tool", "tool_call_id": plan["tc"].id,
			                 "content": observation})
		return messages

	def _append_assistant_reply(self, msg, content: str):
		'''
		Append this turn's closing reply to the history.

		A single-run agent can return without this; a multi-round one cannot — the
		next turn's request must contain what the model answered.
		'''
		reply = {"role": "assistant", "content": content}
		reasoning = getattr(msg, "reasoning_content", None)
		if reasoning is not None:
			# Same echo rule as the tool_calls branch: with thinking on, an empty
			# string must still be passed back — hence `is not None`, not truthiness.
			reply["reasoning_content"] = reasoning
		self.messages.append(reply)

	"""Group related behavior for streaming."""
	def _consume_stream(self, stream, show_content: bool = True):
		'''
		Accumulate a streamed completion into a message object shaped exactly like the
		non-streaming one — .content, .tool_calls (.id / .function.name /
		.function.arguments) and .reasoning_content (only when the server sent that
		channel). Everything downstream (history write-back, tool execution, the
		reasoning echo rule) then works unchanged.

		While the stream arrives, prints it live: reasoning dimmed, content through the
		line-by-line markdown renderer when the terminal supports it (raw deltas
		otherwise, e.g. when piped).
		'''
		render = rendering_enabled()
		printer = MarkdownStream() if render and show_content else None
		content_parts, reasoning_parts = [], []
		tool_calls = {}  # index -> {"id": ..., "name": ..., "arguments": [fragments]}
		reasoning_open, saw_reasoning, saw_content = False, False, False
		usage = None
		finish_reason = None

		for chunk in stream:
			# Read usage *before* the emptiness test. It lands in one of two
			# places depending on the server: riding on the last content chunk
			# (what DeepSeek's own docs describe) or on a separate choices=[]
			# chunk before [DONE] (what OpenAI-compatible gateways emit).
			# Testing `choices` first is the known-broken pattern — it silently
			# drops the number, and the whole context display is built on it.
			if getattr(chunk, "usage", None) is not None:
				usage = chunk.usage
			if not chunk.choices:  # e.g. a trailing usage-only chunk
				continue
			choice = chunk.choices[0]
			# Why the server ended this generation: "stop" (the model finished),
			# "length" (an output cap cut it), "tool_calls", … It is set on the
			# same final chunk that carries the usage count, and it is the only
			# way to tell a complete reply from a truncated one — an empty answer
			# and a finished one are otherwise byte-identical downstream.
			if getattr(choice, "finish_reason", None):
				finish_reason = choice.finish_reason
			delta = choice.delta

			reasoning = getattr(delta, "reasoning_content", None)
			if reasoning is not None:
				saw_reasoning = True
			if reasoning:
				if not reasoning_open:
					print("💭 ", end="", flush=True)
					reasoning_open = True
				reasoning_parts.append(reasoning)
				print(f"\x1b[2m{reasoning}\x1b[22m" if render else reasoning, end="", flush=True)

			if delta.content:
				if show_content and reasoning_open and not saw_content:
					print()  # close the reasoning line before the content starts
				saw_content = True
				content_parts.append(delta.content)
				if show_content:
					if printer:
						rendered = printer.feed(delta.content)
						if rendered:
							print(rendered, end="", flush=True)
					else:
						print(delta.content, end="", flush=True)

			for tc in (delta.tool_calls or []):
				# Tool calls arrive fragmented: id and name once, arguments in pieces.
				# Accumulate per index; the joined string must be the complete JSON.
				slot = tool_calls.setdefault(tc.index, {"id": None, "name": None, "arguments": []})
				if tc.id:
					slot["id"] = tc.id
				if tc.function and tc.function.name:
					slot["name"] = tc.function.name
				if tc.function and tc.function.arguments:
					slot["arguments"].append(tc.function.arguments)

		if printer:
			tail = printer.flush()
			if tail:
				print(tail, end="", flush=True)
		if reasoning_open or (show_content and saw_content):
			print()  # terminate the streamed text

		msg = SimpleNamespace(content="".join(content_parts))
		msg.usage = usage    # None when the server sent no usage channel
		msg.finish_reason = finish_reason    # None when the server reported none
		if saw_reasoning:
			msg.reasoning_content = "".join(reasoning_parts)
		if tool_calls:
			msg.tool_calls = [
				SimpleNamespace(
					id=slot["id"],
					type="function",
					function=SimpleNamespace(name=slot["name"], arguments="".join(slot["arguments"])),
				)
				for _, slot in sorted(tool_calls.items())
			]
		else:
			msg.tool_calls = None
		return msg
