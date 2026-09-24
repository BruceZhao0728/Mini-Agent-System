# Agent that can use tools to complete tasks.

import json
from openai import OpenAI

from config import API_KEY, BASE_URL, MODEL, EXPORT_LOG, EXPORT_LOG_CHOICES, EXPORT_LOG_PATH
from tools import TOOLS, TOOLS_DESC

class Agent:
    '''
    Agent that can use tools to complete tasks.
    
    This agent uses the OpenAI API to interact with a language model. 
    It can call tools based on the model's responses and return the results to the model for further processing. 
    The agent can also log its interactions for debugging or analysis purposes.
    '''
    def __init__(self):
        self.client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    
    def run(self, prompt: str) -> str:
        '''
        Run the agent with a given prompt.

        Args:
            prompt (str): The initial prompt to start the agent's task.
        
        Returns:
            str: The final output from the agent after processing the prompt and any tool calls.
        '''
        messages = [
            {"role": "system", "content": "You are an agent. Use tools to complete tasks."},
            {"role": "user", "content": prompt}
        ]

        round = 0

        if EXPORT_LOG and EXPORT_LOG_CHOICES["initial-messages"]:
            with open(EXPORT_LOG_PATH, "w") as f:
                f.write("Initial messages:\n")
                json.dump(messages, f, indent=4)
                f.write("\n\n")
        
        while True:
            round += 1

            if EXPORT_LOG:
                with open(EXPORT_LOG_PATH, "a") as f:
                    f.write(f"Round {round}\n")
                    f.write(f"--------------------\n")
                    f.write("\n")

            if EXPORT_LOG and EXPORT_LOG_CHOICES["message-beginning"]:
                with open(EXPORT_LOG_PATH, "a") as f:
                    f.write(f"Messages at the beginning:\n")
                    f.write(json.dumps(messages, indent=4))
                    f.write("\n\n")
            
            # LLM Decision
            resp = self.client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS_DESC,
                tool_choice="auto"  # "auto" means the model can choose to call tools or not
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
            
            # No tool call -> return the content
            if not msg.tool_calls:
                return msg.content or "done"
            
            # Tool call -> execute the tool and return the result to LLM
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [{
                    "id": tc.id, 
                    "type": "function", 
                    "function": {
                        "name": tc.function.name, 
                        "arguments": tc.function.arguments
                    }
                } for tc in msg.tool_calls]
            })

            if EXPORT_LOG and EXPORT_LOG_CHOICES["messages-after-llm-response-before-tool-execution"]:
                with open(EXPORT_LOG_PATH, "a") as f:
                    f.write(f"Messages after LLM response and before tool execution:\n")
                    f.write(json.dumps(messages, indent=4))
                    f.write("\n\n")
            
            for tc in msg.tool_calls:
                name = tc.function.name
                args = json.loads(tc.function.arguments)
                
                # Execution
                try:
                    result = TOOLS[name](**args)
                except Exception as e:
                    result = f"error: {e}"
                
                # Return the result to LLM
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result)
                })

            if EXPORT_LOG and EXPORT_LOG_CHOICES["messages-after-tool-execution"]:
                with open(EXPORT_LOG_PATH, "a") as f:
                    f.write(f"Messages after tool execution:\n")
                    f.write(json.dumps(messages, indent=4))
                    f.write("\n\n")