"""Settings, read from the .env in the repo root.

FCA sign-in details are deliberately not here: searching never needs them. Only a future downloader will.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from utils import PROJECT_ROOT

load_dotenv(PROJECT_ROOT.parent.parent / ".env")


@dataclass(frozen=True)
class Settings:
    collection: str
    embedding_model: str
    qdrant_url: str | None
    qdrant_api_key: str | None
    qdrant_path: Path


def get_settings() -> Settings:
    """Read on every call, so tests and notebooks can change the environment."""
    return Settings(
        collection=os.environ.get("HANDBOOK_COLLECTION", "fca_handbook"),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "local:BAAI/bge-small-en-v1.5"),
        qdrant_url=os.environ.get("QDRANT_URL") or None,
        qdrant_api_key=os.environ.get("QDRANT_API_KEY") or None,
        qdrant_path=Path(os.environ.get("QDRANT_PATH") or PROJECT_ROOT / ".qdrant"),
    )
