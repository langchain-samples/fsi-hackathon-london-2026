import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from tests.conftest import SAMPLE
from tools.handbook import HANDBOOK_TOOLS, expand_references, get_provision, lookup_glossary, search_handbook
from utils import store
from utils.schema import Provision, Snapshot
from utils.verification import check_citation

TODAY, NEXT_YEAR = "2026-11-25", "2027-06-01"


def cites(rows):
    return [r["citation"] for r in rows]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("hardcoded password in source code", "XSYS 6.1.1R"),
        ("who approved this change", "XSYS 4.2.1R"),
        ("test before release", "XSYS 4.1.1R"),
        ("retention of audit logs", "XSYS 5.1.1R"),
        ("encryption of customer data", "XSYS 6.1.2R"),
    ],
)
def test_search_finds_the_rule(index, query, expected):
    assert expected in cites(search_handbook.invoke({"query": query, "as_of": TODAY, "limit": 3}))


def test_search_by_reference_puts_the_provision_before_those_citing_it(index):
    assert cites(search_handbook.invoke({"query": "XSYS 4.2.1R", "as_of": TODAY}))[0] == "XSYS 4.2.1R"


def test_filters(index):
    rows = search_handbook.invoke({"query": "customer software", "sourcebooks": ["xprn"], "provision_types": ["G"], "as_of": TODAY})
    assert cites(rows) == ["XPRN 1.1.2G"]


def test_search_and_get_use_the_version_in_force(index):
    for day, years in ((TODAY, "three"), ("2026-12-31", "three"), ("2027-01-01", "five"), (NEXT_YEAR, "five")):
        assert years in get_provision.invoke({"reference": "XSYS 5.1.1R", "as_of": day})["text"]
        rows = [r for r in search_handbook.invoke({"query": "audit logs", "as_of": day}) if r["citation"] == "XSYS 5.1.1R"]
        assert len(rows) == 1 and years in rows[0]["text"]
    assert "not in force" in get_provision.invoke({"reference": "XSYS 5.1.1R", "as_of": "2024-12-31"})


def test_get_respects_the_type(index):
    assert get_provision.invoke({"reference": "xsys 4.1.1 r", "as_of": TODAY})["citation"] == "XSYS 4.1.1R"
    assert "not in force" in get_provision.invoke({"reference": "XSYS 4.1.1G", "as_of": TODAY})


def test_expand_references(index):
    result = expand_references.invoke({"reference": "XSYS 4.1.1R", "as_of": TODAY})
    assert result["cited_by"] == ["XSYS 4.1.2G", "XSYS 4.1.3R"]
    result = expand_references.invoke({"reference": "XSYS 4.1.3R", "as_of": TODAY})
    assert cites(result["cites"]) == ["XSYS 4.1.1R", "XSYS 4.2.1R"]
    missing = expand_references.invoke({"reference": "XSYS 7.1.1R", "as_of": TODAY})
    assert missing["cited_but_not_in_index"] == ["XSYS 99.1.1R"]


def test_glossary(index):
    assert "intolerable harm" in lookup_glossary.invoke({"term": "Important Business Service"})[0]["definition"]
    assert "not a defined term" in lookup_glossary.invoke({"term": "blockchain"})


def test_check_citation(index):
    day = date.fromisoformat(TODAY)
    assert check_citation("XSYS 4.2.1R", "approved by a person other than the person who made it", day) == []
    assert check_citation("XSYS 4.2.1R", "approved by two people", day)
    assert check_citation("XSYS 4.2.9R", "anything", day)
    assert check_citation("XSYS 4.2.1R", "  ", day)


def test_results_are_labelled_as_fictional(index):
    assert get_provision.invoke({"reference": "XSYS 4.1.1R", "as_of": TODAY})["source"] == "fictional-sample"


def test_bad_dates_and_missing_index_are_reported_not_raised(empty_index):
    for tool in HANDBOOK_TOOLS:
        argument = next(iter(tool.args))
        assert "Run scripts/ingest.py" in tool.invoke({argument: "XSYS 4.1.1R"})
    assert "Error" in get_provision.invoke({"reference": "XSYS 4.1.1R", "as_of": "tomorrow"})


def test_changing_the_embedding_model_is_refused(index, monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "openai:other")  # refused before any request is made
    assert "built with hash" in search_handbook.invoke({"query": "audit", "as_of": TODAY})


def test_parallel_searches(index):
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: search_handbook.invoke({"query": "audit logs", "as_of": TODAY}), range(40)))
    assert all(cites(r) == cites(results[0]) for r in results)


def test_index_survives_reopening(index):
    store.close()
    assert get_provision.invoke({"reference": "XSYS 4.1.1R", "as_of": TODAY})["citation"] == "XSYS 4.1.1R"


def _changed_snapshot(tmp_path):
    folder = shutil.copytree(SAMPLE, tmp_path / "new")
    rows = [json.loads(line) for line in (folder / "provisions.jsonl").read_text().splitlines()]
    rows[0]["text"] += " Changed."
    (folder / "provisions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return Snapshot.load(folder)


def test_refresh_moves_the_alias_and_repeat_is_a_no_op(index, tmp_path):
    assert store.build_index(Snapshot.load(SAMPLE), "handbook_sample") == index
    assert store.build_index(_changed_snapshot(tmp_path), "handbook_sample") != index
    assert "Changed." in get_provision.invoke({"reference": "XSYS 4.1.1R", "as_of": TODAY})["text"]


def test_failed_refresh_leaves_the_old_index_live(index, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_payload", lambda *a: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        store.build_index(_changed_snapshot(tmp_path), "handbook_sample")
    assert "Changed." not in get_provision.invoke({"reference": "XSYS 4.1.1R", "as_of": TODAY})["text"]


def test_long_provision_is_split_and_rejoined(empty_index):
    snapshot = Snapshot.load(SAMPLE)
    long_text = "\n".join(f"({n}) the firm must keep record number {n} safely." for n in range(400))
    snapshot.provisions.append(Provision(ref="XSYS 9.1.1R", provision_type="R", text=long_text))
    store.build_index(snapshot, "handbook_sample")
    assert get_provision.invoke({"reference": "XSYS 9.1.1R", "as_of": TODAY})["text"] == long_text
    rows = search_handbook.invoke({"query": "record number 399", "as_of": TODAY})
    assert cites(rows).count("XSYS 9.1.1R") == 1


def test_split_never_exceeds_the_limit():
    parts = store.split_text("word " * 5000, limit=1000)
    assert len(parts) > 1 and all(len(p) <= 1000 for p in parts)


def test_duplicate_provisions_are_rejected(tmp_path):
    folder = shutil.copytree(SAMPLE, tmp_path / "dup")
    path = folder / "provisions.jsonl"
    path.write_text(path.read_text() + path.read_text().splitlines()[0] + "\n")
    with pytest.raises(ValueError, match="twice"):
        Snapshot.load(folder)
