'''
Configuration file for the application
'''

import os

API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-your-key-here")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.getenv("DEEPSEEK_MODEL_NAME", "deepseek-flash")

EXPORT_LOG = True  # Set to False to disable logging of API requests and responses
EXPORT_LOG_CHOICES = {
    "initial-messages": False,
    "message-beginning": True,
    "llm-response": False,
    "llm-response-message": True,
    "messages-after-llm-response-before-tool-execution": True,
    "messages-after-tool-execution": True
}
EXPORT_LOG_DIR = "logs"  # Directory to store log files
EXPORT_LOG_NAME = "deepseek_api.log"  # Name of the log file
EXPORT_LOG_PATH = os.path.join(EXPORT_LOG_DIR, EXPORT_LOG_NAME)