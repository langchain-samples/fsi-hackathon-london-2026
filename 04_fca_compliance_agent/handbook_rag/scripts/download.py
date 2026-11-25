"""Download sourcebooks from the FCA Handbook API into a snapshot folder. Run it from this project's folder:

    uv run --no-sync python -m scripts.download                      # SYSC, PRIN, COBS, CONC and the Glossary
    uv run --no-sync python -m scripts.download --sourcebooks CONC --chapters conc5

Raw responses are kept in `data/snapshots/<date>/raw`, and a run that stops part-way continues from them on the same day. A
partial download gets a folder of its own, such as `<date>-conc-conc5`. The snapshot is written only if every chapter arrived and the checks pass. Then run scripts.ingest on it.
"""

import argparse
import json
from datetime import date
from pathlib import Path

from utils import PROJECT_ROOT
from utils.convert import convert, select_chapters
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


def _check_same_day(folder: Path, day: str) -> None:
    """Raw responses are only continued on the day they were asked for, so a snapshot's date is always true."""
    raw = folder / "raw"
    path = raw / "day.txt"
    if path.exists():
        started = path.read_text(encoding="utf-8").strip()
    elif (raw / "tree.json").exists():  # downloaded before the day was recorded
        manifest = folder / "manifest.json"
        started = json.loads(manifest.read_text(encoding="utf-8"))["snapshot_date"] if manifest.exists() else folder.name
    else:
        started = day
    if started != day:
        raise FCAError(f"{folder} was downloaded for {started}, not {day}. Use another --out.")
    raw.mkdir(parents=True, exist_ok=True)
    path.write_text(day + "\n", encoding="utf-8")


def default_folder(day: str, sourcebooks: list[str], only: list[str] | None = None) -> Path:
    """`<date>` for a full download. A partial one gets a name of its own, so that it never replaces a full one."""
    partial = [*([] if set(sourcebooks) == set(SOURCEBOOKS) else sorted(sourcebooks)), *sorted(only or [])]
    return PROJECT_ROOT / "data" / "snapshots" / "-".join([day, *partial]).lower()


def download(api: HandbookAPI, folder: Path, day: str, sourcebooks: list[str], only: list[str] | None = None) -> None:
    _check_same_day(folder, day)
    raw = folder / "raw"
    tree = _cached(raw / "tree.json", lambda: api.tree(day))
    wanted = select_chapters(tree, sourcebooks, only)
    _cached(raw / "glossary.json", lambda: api.glossary(day))
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
    books = [b.upper() for b in args.sourcebooks]
    folder = args.out or default_folder(day, books, args.chapters)
    try:
        with HandbookAPI(read_token()) as api:
            download(api, folder, day, books, args.chapters)
        report = convert(folder, day, books, args.chapters)
    except (FCAError, ValueError) as error:
        raise SystemExit(f"Stopped: {error}") from None
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
