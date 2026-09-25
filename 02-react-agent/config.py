'''
Configuration file for the application
'''

import os

API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-your-key-here")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.getenv("DEEPSEEK_MODEL_NAME", "deepseek-flash")
THINKING = os.getenv("DEEPSEEK_THINKING", "enabled")  # "enabled" or "disabled" — DeepSeek thinking mode is on by default; set to "disabled" to compare against a plain chat model

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
}
EXPORT_LOG_DIR = "logs"  # Directory to store log files
EXPORT_LOG_NAME = "deepseek_api.log"  # Name of the log file
EXPORT_LOG_PATH = os.path.join(EXPORT_LOG_DIR, EXPORT_LOG_NAME)

MAX_ROUNDS = 30

DEFAULT_SYSTEM_PROMPT = """You are a ReAct agent. Follow this strict format:

Thought: [你的思考过程，分析当前状态，决定下一步]
Action: [工具调用]
Observation: [工具返回结果]

重复 Thought -> Action -> Observation 直到任务完成。
最后输出: Final Answer: [答案]"""