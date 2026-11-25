"""Check that this laptop is ready to run the Control Tower against real models.

    uv run python -m control_tower.doctor        (from 00_control_tower)

It never prints your keys. Each line is a check; fix the first one that fails and run it again.
"""
from __future__ import annotations

import os
import sys

from control_tower.loader import REPO_ROOT

OK, FAIL = "  ok  ", " FAIL "
REQUIRED = ["ATTENDEE_NAME", "LANGSMITH_API_KEY", "LANGSMITH_WORKSPACE_ID"]


def check(label: str, fn) -> bool:
    try:
        detail = fn()
        print(f"[{OK}] {label}{': ' + detail if detail else ''}")
        return True
    except Exception as exc:  # report and carry on, so one run shows every problem
        print(f"[{FAIL}] {label}: {exc}")
        return False


def env_file():
    path = REPO_ROOT / ".env"
    if not path.exists():
        raise RuntimeError(f"no .env in {REPO_ROOT}. Copy .env.example to .env and fill it in")
    from dotenv import load_dotenv
    load_dotenv(path, override=False)
    return str(path)


def env_values():
    missing = [k for k in REQUIRED if not os.environ.get(k) or os.environ[k].startswith("<replace")]
    if missing:
        raise RuntimeError("fill in " + ", ".join(missing) + " in .env")
    return f"model {os.environ.get('MODEL_NAME', 'openai:gpt-6-luna (default)')}, " \
           f"project {os.environ.get('LANGSMITH_PROJECT', '?')}"


def langsmith_auth():
    from langsmith import Client
    next(iter(Client().list_projects(limit=1)), None)
    return "API key accepted"


def model_call():
    from langchain.chat_models import init_chat_model
    from control_tower.settings import MODEL
    reply = init_chat_model(MODEL).invoke("Reply with the single word: ready")
    return f"{MODEL} replied {reply.text.strip()[:40]!r}"


def pillars_load():
    from control_tower.pillars import kyc, reg, tm
    kyc.graph(), tm.project(), reg.reg_agent()
    return "01, 02 and 03 import side by side"


def main() -> int:
    print("Control Tower doctor\n")
    results = [check(".env file", env_file), check(".env values", env_values)]
    if all(results):
        results += [check("LangSmith login", langsmith_auth), check("Model through the LangSmith gateway", model_call)]
    results.append(check("Pillars load", pillars_load))
    print("\nAll good: run `uv run uvicorn portal.app:app --port 8000` and open http://localhost:8000"
          if all(results) else "\nFix the first FAIL above, then run this again.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
