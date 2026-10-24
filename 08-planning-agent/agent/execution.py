"""Tool execution, retry handling, and streamed response assembly."""

import json
import time
import subprocess
from types import SimpleNamespace
from config import (
    EXPORT_LOG, EXPORT_LOG_CHOICES, EXPORT_LOG_PATH, MAX_TOOL_RETRIES,
    TOOL_RETRY_BACKOFF, MAX_TOOL_RETRY_BACKOFF_TIME, MAX_OBS_CHARS,
)
from tools import TOOLS
from utils import clip
from presentation import MarkdownStream, rendering_enabled

RETRIABLE_ERRORS = (subprocess.TimeoutExpired, ConnectionError, TimeoutError)

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

	def _call_tool(self, action_name: str, action_args: dict) -> str:
		'''
		Execute a single tool call and return the observation.

		Args:
			action_name (str): The name of the tool to be called.
			action_args (dict): The arguments to be passed to the tool.

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
					with open(EXPORT_LOG_PATH, "a") as f:
						f.write(f"Tool retry {attempt}/{MAX_TOOL_RETRIES}: {action_name} — {type(e).__name__}: {e}\n")
				
				# Exponential backoff with an upper limit to avoid excessively long waits
				time.sleep(min(TOOL_RETRY_BACKOFF * 2 ** (attempt - 1), MAX_TOOL_RETRY_BACKOFF_TIME))  
			except Exception as e:
				return f"Error: {action_name} failed due to {type(e).__name__}: {e}"

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
