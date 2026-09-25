import json
import subprocess
import time
import logging
from openai import OpenAI, BadRequestError, APIConnectionError, RateLimitError, InternalServerError

from config import API_KEY, BASE_URL, MODEL, THINKING
from config import EXPORT_LOG, EXPORT_LOG_CHOICES, EXPORT_LOG_PATH
from config import MAX_API_RETRIES, API_TIMEOUT
from config import MAX_ROUNDS, MAX_OBS_CHARS, MAX_TOOL_CALLS_PER_ROUND
from config import MAX_TOOL_RETRIES, TOOL_RETRY_BACKOFF, MAX_TOOL_RETRY_BACKOFF_TIME
from config import DEFAULT_SYSTEM_PROMPT

from tools import TOOLS, TOOLS_DESC
from utils import clip

RETRIABLE_ERRORS = (subprocess.TimeoutExpired, ConnectionError, TimeoutError)

class ReActAgent:
	'''
	Agent that can use tools to complete tasks.

	This agent uses the OpenAI API to interact with a language model. 
	It can call tools based on the model's responses and return the results to the model for further processing. 
	The agent can also log its interactions for debugging or analysis purposes.
	'''
	def __init__(self, system_prompt: str = DEFAULT_SYSTEM_PROMPT):
		'''
		Initialize the ReActAgent with a system prompt.

		Args:
			system_prompt (str): The system prompt to guide the agent's behavior.
		'''
		self.client = OpenAI(api_key=API_KEY, base_url=BASE_URL, max_retries=MAX_API_RETRIES, timeout=API_TIMEOUT)
		self.system_prompt = system_prompt
		self.messages = []
		self.plan = []

		if EXPORT_LOG:
			handler = logging.FileHandler(EXPORT_LOG_PATH, mode="a", encoding="utf-8")
			sdk_log = logging.getLogger("openai")
			sdk_log.setLevel(logging.DEBUG)      # Set the logging level to DEBUG to capture detailed information about the SDK's operations
			sdk_log.addHandler(handler)          # Set the logging handler to write logs to the specified file

	def reset(self, prompt: str):
		'''
		Reset the agent's state for a new task.

		Args:
			prompt (str): The initial prompt to start the agent's task.
		'''
		self.messages = [
			{"role": "system", "content": self.system_prompt},
			{"role": "user", "content": prompt}
		]

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
				
    
	def run(self, prompt: str) -> str:
		'''
        Run the agent with a given prompt.

		This agent follows the following pipeline:
		1. Reset the agent's state with the provided prompt.
		2. Enter a loop where it interacts with the language model to get responses.
		3. If the model's response includes tool calls, execute those tools and return the results to the model for further processing.
		4. Continue this process until a final answer is obtained or the maximum number of rounds is reached.

        Args:
            prompt (str): The initial prompt to start the agent's task.
        
        Returns:
            str: The final output from the agent after processing the prompt and any tool calls.
        '''

		self.reset(prompt)

		round = 0
		max_rounds = MAX_ROUNDS  # Prevent infinite loops

		if EXPORT_LOG and EXPORT_LOG_CHOICES["initial-messages"]:
			with open(EXPORT_LOG_PATH, "w") as f:
				f.write("Initial messages:\n")
				json.dump(self.messages, f, indent=4)
				f.write("\n\n")
        
		while round < max_rounds:
			round += 1
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
			try:
				resp = self.client.chat.completions.create(
					model=MODEL,
					messages=self.messages,
					tools=TOOLS_DESC,
					tool_choice="auto",  # "auto" means the model can choose to call tools or not
					extra_body={"thinking": {"type": THINKING}}  # DeepSeek needs thinking inside extra_body; see config.py
				)
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
				return msg
			except (APIConnectionError, RateLimitError, InternalServerError) as e:
				# Implying that the error is temporary, but the SDK has already retried with its own max_retries, so we won't retry here.
				msg = f"Error during LLM call ({type(e).__name__} — SDK retried {self.client.max_retries} times and still failed): {e}"
				print(msg)

				if EXPORT_LOG:
					with open(EXPORT_LOG_PATH, "a") as f:
						f.write(msg + "\n")
						f.write("\n\n")
				return msg
			except Exception as e:
				# Other unexpected errors during the LLM call. 
				# This could be due to various reasons, including network issues or unexpected responses from the API.
				msg = f"Error during LLM call ({type(e).__name__}): {e}"
				print(msg)
				if EXPORT_LOG:
					with open(EXPORT_LOG_PATH, "a") as f:
						f.write(msg + "\n")
						f.write("\n\n")
				return msg
					

			if EXPORT_LOG and EXPORT_LOG_CHOICES["llm-response"]:
				with open(EXPORT_LOG_PATH, "a") as f:
					f.write(f"LLM Response:\n")
					f.write(json.dumps(resp.model_dump(), indent=4))
					f.write("\n\n")
            
			msg = resp.choices[0].message  # The message from the model, which may contain tool calls.

			if EXPORT_LOG and EXPORT_LOG_CHOICES["llm-response-message"]:
				with open(EXPORT_LOG_PATH, "a") as f:
					f.write(f"Message in LLM response:\n")
					f.write(json.dumps(msg.model_dump(), indent=4))
					f.write("\n\n")

			content = msg.content or ""

			if EXPORT_LOG and EXPORT_LOG_CHOICES["llm-response-message-content"]:
				with open(EXPORT_LOG_PATH, "a") as f:
					f.write(f"Content in LLM response message:\n")
					f.write(content)
					f.write("\n\n")

			# Display this round's reasoning. Thinking mode delivers it in reasoning_content;
			# a non-thinking model only has content, so fall back to that round's lead-in line
			# (skipped when the round ends, since [Final]/[Done] already print content).
			# Display-only: a truthiness test is fine here, unlike the history echo below,
			# where an empty string must still be passed back to the API.
			reasoning = getattr(msg, "reasoning_content", None)
			if reasoning:
				print(f"💭 Reasoning: {reasoning}")
			elif content and msg.tool_calls:
				print(f"💭 Lead-in: {content}")
            
            # Display Thought if present
			if "Thought:" in content:
				thought = content.split("Thought:", 1)[1]
				for marker in ("Action:", "Final Answer:"):
					thought = thought.split(marker, 1)[0]
				print(f"🤔 Thought: {thought.strip()}")
				if EXPORT_LOG and EXPORT_LOG_CHOICES["thought"]:
					with open(EXPORT_LOG_PATH, "a") as f:
						f.write(f"Thought:\n")
						f.write(thought.strip())
						f.write("\n\n")

            

            # Check for Final Answer & Judge if the task is completed
			if "Final Answer:" in content:
				print(f"[Final] {content}")
				if EXPORT_LOG and EXPORT_LOG_CHOICES["final-answer"]:
					with open(EXPORT_LOG_PATH, "a") as f:
						f.write(f"Final Answer:\n")
						f.write(content)
						f.write("\n\n")
				return content.split("Final Answer:")[-1].strip()
            
            # No tool call -> return the content
			if not msg.tool_calls:
				print(f"[Done] {content}")
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

			# Max tool calls per round limit. Even if some calls are rejected, a tool message must still be returned —
			# The assistant message contains N tool_calls, and if one result is missing, the next round request will be 400.
			if len(msg.tool_calls) > MAX_TOOL_CALLS_PER_ROUND and EXPORT_LOG and EXPORT_LOG_CHOICES["round-tool-call-cap"]:
				with open(EXPORT_LOG_PATH, "a") as f:
					f.write(f"Round cap: {len(msg.tool_calls)} tool calls requested, "
					        f"executing first {MAX_TOOL_CALLS_PER_ROUND}\n")

			# Execution of tool calls
			for i, tc in enumerate(msg.tool_calls):
				action_name = tc.function.name

				args_len = len(tc.function.arguments or "")
				if EXPORT_LOG and EXPORT_LOG_CHOICES["arguments-size"]:
					with open(EXPORT_LOG_PATH, "a") as f:
						f.write(f"Arguments size: {action_name} {args_len} chars\n")

				if i >= MAX_TOOL_CALLS_PER_ROUND:
					observation = (f"Error: The maximum number of tool calls per round is {MAX_TOOL_CALLS_PER_ROUND}, "
					               f"this call was not executed. Please replan and initiate this call in the next round.")
				else:
					action_args, parse_error = self._parse_args(tc)
					if parse_error:
						observation = parse_error
					else:
						print(f"🔧 Action: {action_name}({action_args})")
						observation = self._call_tool(action_name, action_args)

				# Normalize the observation to ensure it is a string and record its original length before clipping.
				observation = str(observation)
				raw_len = len(observation)
				
				# Clip the observation to ensure it doesn't exceed MAX_OBS_CHARS, preventing overly long context in the message history.
				observation = clip(observation)  

				if EXPORT_LOG and EXPORT_LOG_CHOICES["observation-clipped"]:
					with open(EXPORT_LOG_PATH, "a") as f:
						f.write(f"Observation size: {action_name} {raw_len} chars"
								f"{f' -> clipped to {len(observation)}' if raw_len > MAX_OBS_CHARS else ''}\n")
				
				print(f"👁 Observation: {observation[:200]}{'...' if len(str(observation)) > 200 else ''}")
			
				tool_message = {
					"role": "tool",
					"tool_call_id": tc.id,
					"content": observation
				}
				self.messages.append(tool_message)

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