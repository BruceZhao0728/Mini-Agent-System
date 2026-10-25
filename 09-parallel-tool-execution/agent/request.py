"""Model request construction, streaming, and response metadata logging."""

import json
from openai import (
    BadRequestError, APIConnectionError, RateLimitError, InternalServerError,
)
from config import (
    MODEL, THINKING, REQUEST_USAGE, EXPORT_LOG, EXPORT_LOG_CHOICES,
    EXPORT_LOG_PATH,
)
from tools import TOOLS_DESC
from .helpers import _message_dict

class ModelRequestMixin:
	"""Create model requests and record their response metadata."""
	def _request_completion(self, round_number):
		"""Request and assemble one streamed completion.

		Args:
			round_number: The current ReAct round, used for context logging.
		Returns:
			A (message, error) pair; exactly one item is non-empty.
		"""
		request = {
			"model": MODEL,
			"messages": self.messages,
			"tools": TOOLS_DESC,
			"tool_choice": "auto",  # "auto" means the model can choose to call tools or not
			"stream": True,  # text is printed live; the stream is re-assembled into `msg` below
			"extra_body": {"thinking": {"type": THINKING}},  # DeepSeek needs thinking inside extra_body; see config.py
		}
		if REQUEST_USAGE:
			# The only source of the context number the REPL shows. Gated so an
			# endpoint that rejects the parameter is one config flag away from
			# working again.
			request["stream_options"] = {"include_usage": True}

		try:
			stream = self.client.chat.completions.create(**request)
			msg = self._consume_stream(stream)
		except BadRequestError as e:
			# Deterministic: the request itself is invalid, so retrying won't help. Several causes are
			# possible — the server's own message usually names the actual one; our hint should add only
			# what the server cannot know.
			msg = (f"Error during LLM call (BadRequest, request itself is invalid, retrying won't help; "
			       f"common causes: model name, parameters, context limit, tool schema, history shape/reasoning_content. "
			       f"This is a configuration or code issue in this repository, not a network issue. Server's original message: {e})")
			print(msg)
			
			if EXPORT_LOG:
				with open(EXPORT_LOG_PATH, "a") as f:
					f.write(msg + "\n")
					f.write("\n\n")
			return None, msg
		except (APIConnectionError, RateLimitError, InternalServerError) as e:
			# Implying that the error is temporary, but the SDK has already retried with its own max_retries, so we won't retry here.
			msg = f"Error during LLM call ({type(e).__name__} — SDK retried {self.client.max_retries} times and still failed): {e}"
			print(msg)

			if EXPORT_LOG:
				with open(EXPORT_LOG_PATH, "a") as f:
					f.write(msg + "\n")
					f.write("\n\n")
			return None, msg
		except Exception as e:
			# Other unexpected errors during the LLM call. 
			# This could be due to various reasons, including network issues or unexpected responses from the API.
			msg = f"Error during LLM call ({type(e).__name__}): {e}"
			print(msg)
			if EXPORT_LOG:
				with open(EXPORT_LOG_PATH, "a") as f:
					f.write(msg + "\n")
					f.write("\n\n")
			return None, msg
				

		if getattr(msg, "usage", None) is not None:
			self.last_usage = msg.usage
			if EXPORT_LOG and EXPORT_LOG_CHOICES["llm-usage"]:
				with open(EXPORT_LOG_PATH, "a") as f:
					f.write(f"Usage: prompt={msg.usage.prompt_tokens} "
					        f"completion={msg.usage.completion_tokens} "
					        f"total={msg.usage.total_tokens}\n\n")

		# Why the server ended this generation, as it reported it. The screen
		# warning below is derived from the same value, but recording it every
		# round is what makes a silent truncation findable after the fact —
		# the 2026-10-08 incident could not be diagnosed from its log.
		if EXPORT_LOG and EXPORT_LOG_CHOICES["llm-finish-reason"]:
			with open(EXPORT_LOG_PATH, "a") as f:
				f.write(f"Finish: reason={msg.finish_reason or '<not reported>'}\n\n")

		# One sample per round: this is the line COMPACT.md §7.1's criterion is
		# read from (messages/chars = the local counts; used = the API's number).
		if EXPORT_LOG and EXPORT_LOG_CHOICES["context"]:
			stats = self.context_stats()
			with open(EXPORT_LOG_PATH, "a") as f:
				f.write(f"Context: round={round_number} messages={stats['messages']} "
				        f"chars={stats['chars']} used={stats['used']} "
				        f"window={stats['window']}\n\n")

		if EXPORT_LOG and EXPORT_LOG_CHOICES["llm-response"]:
			with open(EXPORT_LOG_PATH, "a") as f:
				f.write(f"LLM Response (assembled from stream):\n")
				f.write(json.dumps(_message_dict(msg), indent=4))
				f.write("\n\n")
            
		# `msg` is the stream re-assembled into the non-streaming shape (see
		# _consume_stream); there is no SDK response object to model_dump() anymore.

		if EXPORT_LOG and EXPORT_LOG_CHOICES["llm-response-message"]:
			with open(EXPORT_LOG_PATH, "a") as f:
				f.write(f"Message in LLM response:\n")
				f.write(json.dumps(_message_dict(msg), indent=4))
				f.write("\n\n")

		return msg, ""
