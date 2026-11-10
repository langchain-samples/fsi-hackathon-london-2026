"""A short name for you, added to the LangSmith datasets and queues you create so they don't clash with other
attendees' in a shared workspace.

Set ATTENDEE_NAME in .env. If you don't, your computer's username is used.
"""

import getpass
import os
import re

from dotenv import load_dotenv

from utils import PROJECT_ROOT

# The topic's own .env first (where MDA looks for it), then the one shared by all three topics in the repo root.
load_dotenv(PROJECT_ROOT.parent / ".env")

ATTENDEE = re.sub(r"[^a-z0-9]+", "-", (os.environ.get("ATTENDEE_NAME") or getpass.getuser()).lower()).strip("-")
