"""All tests run offline: a scripted model stands in for the LLM, so no API key or network is needed."""
import os
import sys
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "offline")
os.environ["LANGSMITH_TRACING"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_tower import offline  # noqa: E402

offline.enable()
