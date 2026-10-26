"""Context occupancy reporting and transactional history compaction."""

import time
from config import (
    CONTEXT_WINDOW_TOKENS, EXPORT_LOG, EXPORT_LOG_CHOICES, EXPORT_LOG_PATH,
    COMPACT_KEEP_TURNS, COMPACT_KEEP_GROUPS, COMPACT_SUMMARY_MAX_TOKENS,
    COMPACT_SUMMARY_CHUNK_CHARS, COMPACT_TOOL_RESULT_CHARS,
    COMPACT_REASONING_CHARS, COMPACT_SUMMARY_PROMPT, MODEL,
)
from context import (
    split_history, validate_history, build_compacted, render_transcript,
    message_chars, chunk_text, format_compact_result, describe_history,
)

class CompactionMixin:
	"""Group related behavior for context."""
	def context_stats(self) -> dict:
		'''Current context occupancy — from the API's own usage report only.

		total_tokens, not prompt_tokens: prompt_tokens counts the request that was
		sent, which is a *lower bound* on the history as it now stands (this
		round's reply, and any tool results, were appended after it). The last
		round's prompt + completion is the assembled history.

		`used` is None until a request has reported usage, and again right after a
		compaction — the previous number would describe a history that no longer
		exists.

		The number includes the tools=TOOLS_DESC schemas (~5.2K chars, ~1.3K tokens
		for this repo's 12 tools). Not conversation, but it does occupy the window,
		so counting it is the correct reading of "how full is it".
		'''
		used = getattr(self.last_usage, "total_tokens", None)
		return {
			"used": used,
			"window": CONTEXT_WINDOW_TOKENS,
			"ratio": (used / CONTEXT_WINDOW_TOKENS) if used else None,
			"messages": len(self.messages),
			"chars": message_chars(self.messages),
		}

	def _log_section(self, gate: str, text: str):
		'''Append one section to the run log when that gate is on.

		EXPORT_LOG_CHOICES[gate] is indexed, not .get(): a section key missing
		from config.py must fail loudly, which is how this repo keeps the gate
		list and the code reading it in step.
		'''
		if EXPORT_LOG and EXPORT_LOG_CHOICES[gate]:
			with open(EXPORT_LOG_PATH, "a") as f:
				f.write(text)
				f.write("\n\n")

	def _summarize_once(self, transcript: str, previous: str = "") -> str:
		'''One summarization request.

		Deliberately unlike run()'s call: non-streaming (nothing to print live),
		no tools (the answer is prose, and the schemas would cost ~1.3K tokens for
		nothing), thinking off (a summary needs no reasoning channel), and no
		stream_options — that parameter is valid only when stream=True, and
		passing it here is a 400.

		Kept separate from _summarize() so `tests/test_context.py` can substitute it and exercise
		compact()'s failure paths with no network at all.
		'''
		parts = []
		if self.task_text:
			parts.append("The user's original request, verbatim — reproduce it as the "
						 "first item of your summary:\n" + self.task_text)
		if previous:
			parts.append("Summary of the earlier part of this work — treat it as "
						 "established fact and carry the still-relevant parts "
						 "forward:\n" + previous)
		parts.append("Transcript to summarize:\n" + transcript)

		started = time.monotonic()
		span = self.tracer.new_span(self._turn_span)
		request_chars = sum(len(part) for part in parts)
		try:
			response = self.client.chat.completions.create(
				model=MODEL,
				messages=[
					{"role": "system", "content": COMPACT_SUMMARY_PROMPT},
					{"role": "user", "content": "\n\n---\n\n".join(parts)},
				],
				max_tokens=COMPACT_SUMMARY_MAX_TOKENS,
				stream=False,
				extra_body={"thinking": {"type": "disabled"}},
			)
		except Exception as e:
			self.tracer.emit(
				"error", actor="compactor", span_id=span,
				parent_span_id=self._turn_span, component="summarizer",
				error_type=type(e).__name__, error=str(e)[:500],
				retryable=False)
			raise
		content = (response.choices[0].message.content or "").strip()
		self.tracer.emit(
			"model_request", actor="compactor", span_id=span,
			parent_span_id=self._turn_span, component="summarizer",
			model=MODEL, stream=False, message_count=2, tools_count=0,
			request_chars=request_chars,
			duration_ms=round((time.monotonic() - started) * 1000, 1),
			status="ok")
		return content

	def _summarize(self, transcript: str, on_chunk=None) -> str:
		'''Summarize a transcript, folding it through several calls if needed.

		Chunking is by characters, not tokens — this design deliberately has no
		local tokenizer (COMPACT.md §4.2). Folding — summarize part 1, then
		summarize "running summary + part 2", and so on — keeps one prompt shape
		and works at any size; the common case is a single call.

		`on_chunk(i, total, chars)` is called before each request, so the REPL can
		show the fold's progress instead of going quiet for a minute.
		'''
		chunks = chunk_text(transcript, COMPACT_SUMMARY_CHUNK_CHARS)
		summary = ""
		for i, chunk in enumerate(chunks, 1):
			if on_chunk is not None:
				on_chunk(i, len(chunks), len(chunk))
			summary = self._summarize_once(chunk, previous=summary)
			if not summary.strip():
				return ""
		return summary

	def compact(self, verbose: bool = False) -> tuple[bool, str]:
		'''Compress the older part of the history into one summary message.

		Build first, swap last: every failure path returns with self.messages
		untouched. Call this between turns only — never from inside run(). main.py's
		KeyboardInterrupt rollback is an index snapshot into this list
		(`del agent.messages[start:]`), so rewriting the list underneath that
		snapshot can silently truncate the tail it meant to keep.

		`verbose=True` narrates the whole thing — what is summarized, what is kept,
		the fold's progress, the summary text, and the resulting history — because
		/compact is a deliberate user action, and "what did it actually keep?" is
		the only question worth asking about it. **In verbose mode this method
		prints everything including the success line**, so a caller that asked for
		verbose output should print the returned message only when the result is
		False (the failure paths never print).
		'''
		def say(text=""):
			"""Perform the say operation. Args: text: Value used by this operation. Returns: The result of this operation."""
			if verbose:
				print(text)

		# A blank line first: the REPL prompt is printed without a trailing
		# newline (`[3] you > `), so this multi-line output would otherwise
		# start on the prompt's own line.
		say()
		say("Compacting the context…")
		before_count = len(self.messages)
		before_chars = message_chars(self.messages)

		cut = split_history(self.messages, COMPACT_KEEP_TURNS, COMPACT_KEEP_GROUPS)
		if cut is None:
			return False, "Nothing to compact (the history is too short, or only the last few turns are left)"

		transcript = render_transcript(
			self.messages[1:cut],
			tool_chars=COMPACT_TOOL_RESULT_CHARS,
			reasoning_chars=COMPACT_REASONING_CHARS,
		)
		say(f"  summarize      messages[1:{cut}] — {cut - 1} messages, "
			f"{message_chars(self.messages[1:cut]):,} chars")
		say(f"  keep verbatim  messages[{cut}:] — {before_count - cut} messages, plus the system prompt")
		say(f"  as a transcript {len(transcript):,} chars "
			f"(tool results clipped to {COMPACT_TOOL_RESULT_CHARS} chars each, "
			f"reasoning to {COMPACT_REASONING_CHARS})")

		def progress(index, total, chars):
			"""Perform the progress operation. Args: index: Value used by this operation; total: Value used by this operation; chars: Value used by this operation. Returns: The result of this operation."""
			say(f"  summarization call {index}/{total} ({chars:,} chars in this chunk)…")

		try:
			summary = self._summarize(transcript, on_chunk=progress)
		except Exception as e:
			return False, f"Compaction failed (the summarization call raised; history untouched): {type(e).__name__}: {e}"

		if not summary.strip():
			return False, "Compaction failed (the summary was empty; history untouched)"

		candidate = build_compacted(self.messages, cut, summary)

		problems = validate_history(candidate)
		self.tracer.emit(
			"verification", actor="compactor",
			span_id=self.tracer.new_span(self._turn_span),
			parent_span_id=self._turn_span, check="compaction_history",
			status="fail" if problems else "pass", layer="structure",
			reason="; ".join(problems[:3])[:500])
		if problems:
			self._log_section("compact", "Compact REJECTED — illegal history:\n  "
							  + "\n  ".join(problems))
			return False, f"Compaction failed (the resulting history is illegal, discarded): {problems[0]}"

		after_chars = message_chars(candidate)
		if after_chars >= before_chars:
			self._log_section("compact", "Compact REJECTED — summary is not smaller: "
							  f"{before_chars} -> {after_chars} chars")
			return False, (f"Compaction failed (the summary is longer than what it replaces: "
						   f"{before_chars:,} → {after_chars:,} chars; history untouched)")

		self.messages = candidate
		self.last_usage = None   # the measurement described the history just replaced
		map_lines = describe_history(self.messages)
		self._log_section("compact",
			f"Compact:\n  cut={cut}  messages {before_count} -> {len(candidate)}\n"
			f"  chars {before_chars} -> {after_chars}\n"
			f"  summarized messages[1:{cut}], kept messages[{cut}:]\n"
			f"  budgets: tool result {COMPACT_TOOL_RESULT_CHARS} chars, reasoning "
			f"{COMPACT_REASONING_CHARS} chars, chunk {COMPACT_SUMMARY_CHUNK_CHARS} chars\n"
			f"  summary:\n{summary}\n"
			f"  resulting history:\n" + "\n".join("  " + line for line in map_lines))

		result = format_compact_result(before_count, len(candidate),
									   before_chars, after_chars)
		if verbose:
			say()
			say(result)
			say()
			say(f"Summary ({len(summary):,} chars, replacing the {cut - 1} messages above):")
			say(summary)
			say()
			say("The compacted history:")
			for line in map_lines:
				say(line)
		return True, result
