"""Build the Handbook index from a snapshot folder. Run it from `04_fca_compliance_agent/handbook_rag`:

    uv run --no-sync python -m scripts.ingest data/sample --alias fca_handbook_sample
"""

import argparse
from pathlib import Path

from utils import store
from utils.schema import Snapshot
from utils.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--alias", default=None, help="index name; defaults to HANDBOOK_COLLECTION")
    args = parser.parse_args()

    settings = get_settings()
    alias = args.alias or settings.collection
    snapshot = Snapshot.load(args.snapshot)
    if snapshot.manifest.source == "fictional-sample" and not alias.endswith("_sample"):
        raise SystemExit("Fictional sample data may only go into an index whose name ends in _sample.")
    try:
        name = store.build_index(snapshot, alias)
    finally:
        store.close()
    print(f"{alias} -> {name}: {len(snapshot.provisions)} provisions, {len(snapshot.glossary)} glossary terms "
          f"({settings.embedding_model})")  # fmt: skip


if __name__ == "__main__":
    main()
