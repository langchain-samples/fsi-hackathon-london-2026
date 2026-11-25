"""A tiny read-only database over the JSONL files in `data/`.

Each `.jsonl` file is a table and each line in it is one row, so `data/transactions.jsonl` is the `transactions`
table. Open the files to read or change the rows. Every query reads the file again, so your changes show up
straight away, without restarting anything.
"""

import json

from utils import PROJECT_ROOT

DATA_DIR = PROJECT_ROOT / "data"


def table(name: str) -> list[dict]:
    """Every row in a table, in file order."""
    with open(DATA_DIR / f"{name}.jsonl", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def query(name: str, **where) -> list[dict]:
    """The rows whose fields equal the given values, for example `query("transactions", user_id="USR-001")`."""
    return [row for row in table(name) if all(row.get(field) == value for field, value in where.items())]


def get(name: str, **where) -> dict | None:
    """The first row that matches, or None if there isn't one."""
    rows = query(name, **where)
    return rows[0] if rows else None
