"""Try a search from the terminal, with the MCP server stopped. Run it from `04_fca_compliance_agent/handbook_rag`:

    uv run --no-sync python -m scripts.search "testing changes before release"
"""

import argparse

from tools.handbook import search_fca_handbook
from utils import store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query")
    parser.add_argument("--sourcebook", action="append")
    parser.add_argument("--as-of", default=None)
    args = parser.parse_args()
    try:
        found = search_fca_handbook(args.query, sourcebooks=args.sourcebook, as_of=args.as_of)
    except (store.IndexNotReady, ValueError) as error:
        raise SystemExit(f"Error: {error}") from None
    finally:
        store.close()
    if found["not_in_index"]:
        print(f"Not in the index: {', '.join(found['not_in_index'])}")
    for row in found["results"]:
        print(f"{row['reference']}  [{row['source']}]  {row['title']}\n    {row['text'][:160]}\n")


if __name__ == "__main__":
    main()
