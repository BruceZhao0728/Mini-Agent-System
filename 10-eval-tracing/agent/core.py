"""Concrete ReAct agent state and initialization."""

import logging
from openai import OpenAI
import memory
import tracing
from config import (
    API_KEY, BASE_URL, MAX_API_RETRIES, API_TIMEOUT, DEFAULT_SYSTEM_PROMPT,
    EXPORT_LOG, EXPORT_LOG_PATH, MEMORY_INDEX_MAX_NOTES, MEMORY_INDEX_MAX_CHARS,
    MEMORY_INDEX_HEADER, PLAN_MAX_STEPS,
    TRACE_ENABLED, TRACE_PATH, TRACE_MAX_BYTES, TRACE_KEEP,
    TRACE_FIELD_CHARS, TRACE_INCLUDE_CONTENT,
)
from .planning import PlanningMixin
from .compaction import CompactionMixin
from .execution import ExecutionMixin
from .orchestration import TaskOrchestrationMixin
from .react_loop import ReActLoopMixin
from .request import ModelRequestMixin

class ReActAgent(PlanningMixin, CompactionMixin, ExecutionMixin, TaskOrchestrationMixin, ModelRequestMixin, ReActLoopMixin):
	'''
	Agent that can use tools to complete tasks.

	This agent uses the OpenAI API to interact with a language model. 
	It can call tools based on the model's responses and return the results to the model for further processing. 
	The agent can also log its interactions for debugging or analysis purposes.
	'''
	def __init__(self, system_prompt: str = DEFAULT_SYSTEM_PROMPT,
	             client=None, tracer=None):
		'''
		Initialize the ReActAgent with a system prompt.

		Args:
			system_prompt (str): The system prompt to guide the agent's behavior.
			client: Optional OpenAI-compatible client; tests and eval inject a fake.
			tracer: Optional tracer; None means build one from config.
		'''
		self.client = client if client is not None else OpenAI(
			api_key=API_KEY, base_url=BASE_URL, max_retries=MAX_API_RETRIES, timeout=API_TIMEOUT
		)
		self.tracer = tracer if tracer is not None else tracing.get_tracer(
			enabled=TRACE_ENABLED, path=TRACE_PATH,
			max_bytes=TRACE_MAX_BYTES, keep=TRACE_KEEP,
			max_field_chars=TRACE_FIELD_CHARS,
			include_content=TRACE_INCLUDE_CONTENT)
		self.system_prompt = system_prompt
		self.messages = []
		self.plan = []
		self.turn = 0
		self._rounds_used = 0
		self.last_usage = None   # usage from the most recent API call; None = not measured
		self.task_text = None    # the first user message, kept verbatim for the summarizer
		self.memory_index = ""   # long-term memory index spliced into the system prompt by reset()
		self.memory_index_error = None   # why it is empty, when that was not simply "no notes"
		self.plan_request = None
		self.plan_error = None
		self._turn_span = None
		self._turn_started = 0.0
		self._plan_step = None
		self._plan_step_span = None
		self._round = 0
		self._round_started = 0.0
		self._round_span = None


		if EXPORT_LOG:
			handler = logging.FileHandler(EXPORT_LOG_PATH, mode="a", encoding="utf-8")
			sdk_log = logging.getLogger("openai")
			sdk_log.setLevel(logging.DEBUG)      # Set the logging level to DEBUG to capture detailed information about the SDK's operations
			sdk_log.addHandler(handler)          # Set the logging handler to write logs to the specified file

	def reset(self):
		'''
		Start a new conversation: the history keeps only the system prompt.

		Do NOT call this between turns of a multi-round session — that is exactly
		what throws the conversation away.

		This is also where long-term memory is loaded. The index is *rebuilt* here
		rather than at __init__ so that "start a new conversation" means "re-read
		the store", and so self.system_prompt stays the untouched base prompt.

		The index rides inside messages[0], which buys two properties for free:
		split_history's keep_head=1 protects it from every future compaction, and
		it never enters the rolling token budget the way a second message would.
		It must not be a message of its own — the summarizer's range starts at
		messages[1] and would swallow it on the first compaction.
		'''
		try:
			self.memory_index = memory.render_index(
				path=memory.NOTES_PATH, max_notes=MEMORY_INDEX_MAX_NOTES,
				max_chars=MEMORY_INDEX_MAX_CHARS)
			self.memory_index_error = None
		except Exception as e:
			# Non-fatal on purpose (an unreadable store must not make the agent
			# unusable) but never silent: the reason is recorded under the
			# "memory" log gate, which is the debugging surface for this feature.
			self.memory_index = ""
			self.memory_index_error = f"{type(e).__name__}: {e}"

		content = self.system_prompt
		if self.memory_index:
			content += MEMORY_INDEX_HEADER + self.memory_index
		self.messages = [{"role": "system", "content": content}]
		self.turn = 0
		self.last_usage = None   # occupancy describes the current history, not the last one
		self.task_text = None

		self.plan = []
		self.plan_request = None
		self.plan_error = None
		self._rounds_used = 0
		self._turn_span = None
		self._turn_started = 0.0
		self._plan_step = None
		self._plan_step_span = None
		self._round = 0
		self._round_started = 0.0
		self._round_span = None


