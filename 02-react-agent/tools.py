# Tools for Agent to call.

import os
import subprocess
from typing import Callable, Dict

def read_file(path: str) -> str:
    '''
    Read content from a file.
    
    Args:
        path (str): The path to the file to read.

    Returns:
        str: The content of the file.
    '''
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def write_file(path: str, content: str) -> str:
    '''
    Write content to a file.

    Args:
        path (str): The path to the file to write.
        content (str): The content to write to the file.

    Returns:
        str: A message indicating the file has been written.
    '''
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return f"written: {path}"

def execute_command(command: str) -> str:
    '''
    Execute a shell command and return the output.

    Args:
        command (str): The shell command to execute.

    Returns:
        str: The combined stdout and stderr output of the command.
    '''
    result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
    return result.stdout + result.stderr


TOOLS: Dict[str, Callable] = {
    "read_file": read_file,
    "write_file": write_file,
    "execute_command": execute_command,
}

TOOLS_DESC = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read content from a file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "Execute a shell command and return the output",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"]
            }
        }
    }
]