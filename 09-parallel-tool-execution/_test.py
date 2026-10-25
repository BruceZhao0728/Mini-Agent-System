"""Run all offline assertion groups in a stable order."""

import os
os.makedirs("logs", exist_ok=True)

from tests import (
    test_tools, test_context, test_presentation, test_responses,
    test_memory, test_cli, test_planning, test_parallel,
)

print("\nAll offline assertion groups passed.")
