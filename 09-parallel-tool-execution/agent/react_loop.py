"""Single-step ReAct request, tool-call, and response loop."""

import json
from config import (
    MAX_ROUNDS, EXPORT_LOG, EXPORT_LOG_CHOICES, EXPORT_LOG_PATH,
    MEMORY_INDEX_MAX_NOTES, MEMORY_INDEX_MAX_CHARS,
)
from .helpers import _answer_warning

class ReActLoopMixin:
	"""Run one ReAct step until it finishes or reaches its round limit."""
	def _run_react(self, prompt: str, first_turn: bool = False) -> str:
		"""
        Run the agent with a given prompt.

		This agent follows the following pipeline:

		- Reset the agent state on the first turn; later turns append to the existing history.
		- Enter a loop where it interacts with the language model to get responses.
		- If the model response includes tool calls, execute those tools and return the results to the model for further processing.
		- Continue this process until a final answer is obtained or the maximum number of rounds is reached.

        Args:
            prompt (str): The initial prompt to start the agent's task.

        Returns:
            str: The final output from the agent after processing the prompt and any tool calls.
        """

		# Last line of defense against lone surrogates: terminal input can carry
		# invalid UTF-8 bytes (stdin decodes with surrogateescape — e.g. backspacing
		# over a CJK char on a pty without IUTF8 leaves orphan bytes behind). Those
		# cannot be encoded as strict UTF-8, so the first log write would crash the
		# whole process. Identity for well-formed text; bad bytes become U+FFFD.
		prompt = prompt.encode("utf-8", "surrogateescape").decode("utf-8", "replace")

		if self.task_text is None:
			self.task_text = prompt
		self.messages.append({"role": "user", "content": prompt})

		round = 0
		max_rounds = max(0, MAX_ROUNDS - self._rounds_used)	# Shared across all plan steps in this user request.

		if first_turn and EXPORT_LOG and EXPORT_LOG_CHOICES["initial-messages"]:
			with open(EXPORT_LOG_PATH, "w") as f:
				f.write("Initial messages:\n")
				json.dump(self.messages, f, indent=4)
				f.write("\n\n")

		# The memory index is the one part of this feature no existing gate
		# captures: it is built in reset() and lives inside messages[0], so the
		# history dumps show it as an undifferentiated blob of system prompt.
		# write_note / recall_notes need no gate of their own — their results
		# are tool messages, already dumped by "messages-after-tool-execution".
		if first_turn and EXPORT_LOG and EXPORT_LOG_CHOICES["memory"]:
			with open(EXPORT_LOG_PATH, "a") as f:
				f.write("Memory: index at session start\n")
				f.write("--------------------\n")
				if self.memory_index_error:
					f.write(f"FAILED to build the index — {self.memory_index_error}\n")
				else:
					f.write(f"{len(self.memory_index):,} chars "
							f"(cap {MEMORY_INDEX_MAX_NOTES} notes / {MEMORY_INDEX_MAX_CHARS} chars)\n")
					f.write(self.memory_index + "\n" if self.memory_index
							else "(store is empty — no notes yet)\n")
				f.write("\n")

		while round < max_rounds:
			round += 1
			self._rounds_used += 1
			print(f"--- Round {round} ---")

			if EXPORT_LOG:
				with open(EXPORT_LOG_PATH, "a") as f:
					f.write(f"Round {round}\n")
					f.write(f"--------------------\n")
					f.write("\n")

			if EXPORT_LOG and EXPORT_LOG_CHOICES["message-beginning"]:
				with open(EXPORT_LOG_PATH, "a") as f:
					f.write(f"Messages at the beginning:\n")
					f.write(json.dumps(self.messages, indent=4))
					f.write("\n\n")
            
            # LLM Decision
			msg, request_error = self._request_completion(round)
			if request_error:
				return request_error
			content = msg.content or ""

			if EXPORT_LOG and EXPORT_LOG_CHOICES["llm-response-message-content"]:
				with open(EXPORT_LOG_PATH, "a") as f:
					f.write(f"Content in LLM response message:\n")
					f.write(content)
					f.write("\n\n")

			# A closing reply that was cut short, or never written at all, must not
			# be reported as an answer. `[Final]` and `[Done]` below say "this round
			# is over", not "the answer arrived" — this line is what separates the
			# two. Tool-calling rounds are exempt: there a bare "Thought: …" is the
			# normal shape, and _answer_warning assumes a closing reply.
			if not msg.tool_calls:
				warning = _answer_warning(content, msg.finish_reason)
				if warning:
					print(warning)
					if EXPORT_LOG and EXPORT_LOG_CHOICES["llm-finish-reason"]:
						with open(EXPORT_LOG_PATH, "a") as f:
							f.write(f"Finish warning: {warning}\n\n")

			# Reasoning and content were already printed live while streaming (see
			# _consume_stream) — this round has nothing left to echo for display.
            
            # Display Thought if present
			if "Thought:" in content:
				thought = content.split("Thought:", 1)[1]
				for marker in ("Action:", "Final Answer:"):
					thought = thought.split(marker, 1)[0]
				# Not printed: the content was streamed live above. Still logged.
				if EXPORT_LOG and EXPORT_LOG_CHOICES["thought"]:
					with open(EXPORT_LOG_PATH, "a") as f:
						f.write(f"Thought:\n")
						f.write(thought.strip())
						f.write("\n\n")

            

            # Check for Final Answer & Judge if the task is completed
			if "Final Answer:" in content:
				print("[Final]")
				if EXPORT_LOG and EXPORT_LOG_CHOICES["final-answer"]:
					with open(EXPORT_LOG_PATH, "a") as f:
						f.write(f"Final Answer:\n")
						f.write(content)
						f.write("\n\n")
				self._append_assistant_reply(msg, content)
				return content.split("Final Answer:")[-1].strip()
            
            # No tool call -> return the content
			if not msg.tool_calls:
				print("[Done]")
				self._append_assistant_reply(msg, content)
				return content

            # Note that the assistant message should be constructed outside of the tool execution loop
            # in order to save context in the message history.

			assistant_message = {
				"role": "assistant",
				"content": content,  # Note that this may be too long
				"tool_calls": [{
					"id": tc.id, 
					"type": "function",
					"function": {
						"name": tc.function.name, 
						"arguments": tc.function.arguments  # Note that this may be too long
					}
				} for tc in msg.tool_calls]
			}

			reasoning = getattr(msg, "reasoning_content", None)
			if reasoning is not None:
				# Add reasoning content to the assistant message if it exists
				# DeepSeek thinking requires reasoning content in the message history if the assistant has tool calls.
				assistant_message["reasoning_content"] = reasoning  # Note that this may be too long
			self.messages.append(assistant_message)

			# The helper applies the per-round cap, parses arguments, runs
			# contiguous safe groups in a pool, and returns tool messages in
			# tool_calls order. History is extended only after it succeeds.
			self.messages.extend(self._execute_tool_calls(msg.tool_calls))

			if EXPORT_LOG and EXPORT_LOG_CHOICES["messages-after-tool-execution"]:
				with open(EXPORT_LOG_PATH, "a") as f:
					f.write(f"Messages after tool execution:\n")
					f.write(json.dumps(self.messages, indent=4))
					f.write("\n\n")

		if EXPORT_LOG:
			with open(EXPORT_LOG_PATH, "a") as f:
				f.write(f"Max steps reached. Exiting.\n")
				f.write("\n\n")

		return "Max steps reached"

