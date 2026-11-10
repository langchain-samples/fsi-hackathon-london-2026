"""The chat model used by every module and agent in this workshop.

The model is set by `MODEL_NAME` (as `provider:model`) in the .env in the repo root, and falls back to
`openai:gpt-6-luna` if it isn't set. The provider's client reads its gateway URL and key from .env on its own (for
example `OPENAI_BASE_URL` and `OPENAI_API_KEY`), so you don't need to edit any code. Providers other than OpenAI and
Anthropic need their package first, for example `uv add langchain-aws`.
"""

import os

from dotenv import load_dotenv

from utils import PROJECT_ROOT

load_dotenv(PROJECT_ROOT.parent / ".env")

model = os.environ.get("MODEL_NAME", "openai:gpt-6-luna")
