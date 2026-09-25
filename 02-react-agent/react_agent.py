# Agent that can use tools to complete tasks.

import json
from openai import OpenAI

from config import API_KEY, BASE_URL, MODEL, THINKING, EXPORT_LOG, EXPORT_LOG_CHOICES, EXPORT_LOG_PATH, MAX_ROUNDS, DEFAULT_SYSTEM_PROMPT
from tools import TOOLS, TOOLS_DESC

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
			system_prompt (str): The system prompt to guide the agent's behavior. Defaults to DEFAULT_SYSTEM_PROMPT.
		'''
		self.client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
		self.system_prompt = system_prompt
		self.messages = []
		self.plan = []

	def reset(self, prompt: str):
		'''
		Reset the agent's state with a new prompt.

		Args:
			prompt (str): The new prompt to start the agent's task.
		'''
		self.messages = [
			{"role": "system", "content": self.system_prompt},
			{"role": "user", "content": prompt}
		]
        
    
	def run(self, prompt: str) -> str:
		'''
        Run the agent with a given prompt.

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
			resp = self.client.chat.completions.create(
				model=MODEL,
				messages=self.messages,
				tools=TOOLS_DESC,
				tool_choice="auto",  # "auto" means the model can choose to call tools or not
				extra_body={"thinking": {"type": THINKING}}  # DeepSeek needs thinking inside extra_body; see config.py
			)

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
				"content": content,
				"tool_calls": [{
					"id": tc.id, 
					"type": "function",
					"function": {
						"name": tc.function.name, 
						"arguments": tc.function.arguments
					}
				} for tc in msg.tool_calls]
			}

			reasoning = getattr(msg, "reasoning_content", None)
			if reasoning is not None:
				# Add reasoning content to the assistant message if it exists
				# DeepSeek thinking requires reasoning content in the message history if the assistant has tool calls.
				assistant_message["reasoning_content"] = reasoning
			self.messages.append(assistant_message)

			# Execution of tool calls
			for tc in msg.tool_calls:
				action_name = tc.function.name

				try:
					action_args = json.loads(tc.function.arguments or "{}")
				except json.JSONDecodeError as e:
					observation = f"Error: Invalid JSON format in arguments ({e})"
				else:
					print(f"🔧 Action: {action_name}({action_args})")
				
					# Execute the tool and handle any exceptions that may occur during execution
					try:
						observation = TOOLS[action_name](**action_args)
					except Exception as e:
						observation = f"Error: {e}"

				print(f"👁 Observation: {observation[:200]}{'...' if len(str(observation)) > 200 else ''}")
			
				tool_message = {
					"role": "tool",
					"tool_call_id": tc.id,
					"content": str(observation)
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