"""Download sourcebooks from the FCA Handbook API into a snapshot folder. Run it from this project's folder:

    uv run --no-sync python -m scripts.download                      # SYSC, PRIN, COBS, CONC and the Glossary
    uv run --no-sync python -m scripts.download --sourcebooks CONC --chapters conc5

Raw responses are kept in `data/snapshots/<date>/raw`, and a run that stops part-way continues from them. The snapshot is
written only if every chapter arrived and the checks pass. Then run scripts.ingest on it.
"""

import argparse
import json
from datetime import date
from pathlib import Path

from utils import PROJECT_ROOT
from utils.convert import chapters_of, convert
from utils.fca_api import FCAError, HandbookAPI, read_token

SOURCEBOOKS = ["SYSC", "PRIN", "COBS", "CONC"]


def _cached(path: Path, fetch) -> dict:
    """A response, from disk if an earlier run already has it. Written whole, so a crash leaves no half file."""
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    found = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    partial.write_text(json.dumps(found, ensure_ascii=False), encoding="utf-8")
    partial.rename(path)
    return found


def download(api: HandbookAPI, folder: Path, day: str, sourcebooks: list[str], only: list[str] | None = None) -> None:
    raw = folder / "raw"
    tree = _cached(raw / "tree.json", lambda: api.tree(day))
    _cached(raw / "glossary.json", lambda: api.glossary(day))
    wanted = [c for c in chapters_of(tree, sourcebooks) if not only or c["id"] in only]
    if not wanted:
        raise FCAError("none of those sourcebooks or chapters is in the Handbook")
    for number, chapter in enumerate(wanted, 1):
        path = raw / "chapters" / f"{chapter['id']}.json"
        fresh = not path.exists()
        found = _cached(path, lambda c=chapter: api.chapter(c["id"], day) or {"provision": []})
        if fresh:
            print(f"[{number}/{len(wanted)}] {chapter['id']}: {len(found['provision'])} provisions", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sourcebooks", nargs="+", default=SOURCEBOOKS)
    parser.add_argument("--chapters", nargs="+", default=None, help="only these chapter IDs, for example conc5")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    day = date.today().isoformat()
    folder = args.out or PROJECT_ROOT / "data" / "snapshots" / day
    books = [b.upper() for b in args.sourcebooks]
    try:
        with HandbookAPI(read_token()) as api:
            download(api, folder, day, books, args.chapters)
        report = convert(folder, day, books, args.chapters)
    except (FCAError, ValueError) as error:
        raise SystemExit(f"Stopped: {error}") from None
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
