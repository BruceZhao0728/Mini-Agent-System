'''
Configuration file for the application
'''

import time
import os

current_time = time.localtime()
current_time_str = time.strftime("%Y%m%d_%H%M%S", current_time)

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
	"observation-clipped": True,
	"tool-retry": True,
	"round-tool-call-cap": True,
	"arguments-size": True
}
EXPORT_LOG_DIR = "logs"  # Directory to store log files
EXPORT_LOG_NAME_TEMPLATE = "deepseek_log_{current_time_str}.log"
EXPORT_LOG_PATH = os.path.join(
    EXPORT_LOG_DIR, 
    EXPORT_LOG_NAME_TEMPLATE.format(current_time_str=current_time_str)
)

MAX_API_RETRIES = 3
API_TIMEOUT = 60	# Timeout for API requests in seconds

MAX_ROUNDS = 30
MAX_OBS_CHARS = 8000				# Maximum number of characters from tool output to be included in history per round
MAX_TOOL_CALLS_PER_ROUND = 5		# Maximum number of tool calls allowed per round

MAX_TOOL_RETRIES = 3
TOOL_RETRY_BACKOFF = 0.5			# 0.5 seconds of exponential backoff for tool retries
MAX_TOOL_RETRY_BACKOFF_TIME = 10		# Maximum backoff time for tool retries

DEFAULT_SYSTEM_PROMPT = """You are a ReAct agent. Follow this strict format:

Thought: [你的思考过程，分析当前状态，决定下一步]
Action: [工具调用]
Observation: [工具返回结果]

重复 Thought -> Action -> Observation 直到任务完成。
最后输出: Final Answer: [答案]"""