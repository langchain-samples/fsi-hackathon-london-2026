"""The FCA Handbook API (https://handbook.fca.org.uk/handbook-api), used only to download data.

Nothing that searches imports this, so an agent's process never holds the token. The token is read from `.env.fca`, is not
put in the environment, and is only ever sent to `HOST`: redirects are refused.
"""

import json
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

from utils import PROJECT_ROOT

HOST = "https://api.handbook.fca.org.uk"
CREDENTIALS = PROJECT_ROOT.parent.parent / ".env.fca"
_PAUSE = 1.1  # the limit is 60 requests a minute for each endpoint
_RETRIES = 6


class FCAError(RuntimeError):
    """The API refused a request. The message never contains the token."""


def read_token(path: Path = CREDENTIALS) -> str:
    token = dotenv_values(path).get("FCA_HANDBOOK_API_TOKEN") if path.exists() else None
    if not token:
        raise FCAError(f"FCA_HANDBOOK_API_TOKEN is not set in {path}")
    return token


class HandbookAPI:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None, pause: float = _PAUSE) -> None:
        self._client = httpx.Client(base_url=HOST, headers={"Authorization": f"Bearer {token}"}, timeout=30,
                                    follow_redirects=False, transport=transport,
                                    # The server stalls on reused connections.
                                    limits=httpx.Limits(max_keepalive_connections=0))  # fmt: skip
        self._pause = pause

    def __enter__(self) -> "HandbookAPI":
        return self

    def __exit__(self, *_: object) -> None:
        self._client.close()

    def _post(self, path: str, body: dict) -> dict | None:
        """The response, or None for 404, which the API uses for a chapter with nothing in force."""
        for attempt in range(_RETRIES):
            try:
                response = self._client.post(path, json=body)
            except httpx.TransportError as error:
                reason = type(error).__name__
                wait = 2.0**attempt
            else:
                if response.status_code == 200:
                    time.sleep(self._pause)
                    return response.json()
                if response.status_code == 404:
                    time.sleep(self._pause)
                    return None
                reason = f"HTTP {response.status_code}"
                if response.status_code != 429 and response.status_code < 500:
                    break
                given = response.headers.get("Retry-After", "")
                wait = float(given) if given.isdigit() else 2.0**attempt
            if attempt + 1 < _RETRIES:
                print(f"  {path}: {reason}, trying again", flush=True)
                time.sleep(min(wait, 90) if self._pause else 0)
        raise FCAError(f"{path} {json.dumps(body)} failed: {reason}")

    def tree(self, day: str) -> dict:
        found = self._post("/get_all_handbook_tree", {"timeline_date": day})
        if found is None:
            raise FCAError(f"the API has no Handbook for {day}")
        return found

    def chapter(self, chapter_id: str, day: str) -> dict | None:
        return self._post("/get_latest_handbook_provisions", {"chapter_id": chapter_id, "timeline_date": day})

    def glossary(self, day: str) -> dict:
        found = self._post("/get_latest_glossary", {"timeline_date": day})
        if found is None:
            raise FCAError(f"the API has no Glossary for {day}")
        return found
