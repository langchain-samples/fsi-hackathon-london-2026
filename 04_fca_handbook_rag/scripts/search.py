"""Try a search from the terminal. Run it from `04_fca_handbook_rag`:

    uv run python -m scripts.search "testing changes before release"
"""

import argparse

from tools.handbook import search_handbook
from utils import store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query")
    parser.add_argument("--sourcebook", action="append")
    parser.add_argument("--as-of", default=None)
    args = parser.parse_args()
    result = search_handbook.invoke({"query": args.query, "sourcebooks": args.sourcebook, "as_of": args.as_of})
    store.close()
    if isinstance(result, str):
        raise SystemExit(result)
    for row in result:
        print(f"{row['citation']}  [{row['source']}]  {' > '.join(row['heading_path'])}\n    {row['text'][:160]}\n")


if __name__ == "__main__":
    main()
