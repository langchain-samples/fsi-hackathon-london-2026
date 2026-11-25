import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from tests.conftest import SAMPLE
from tools import handbook
from tools.handbook import check_fca_citation, get_fca_section, search_fca_handbook
from utils import store
from utils.schema import Provision, Snapshot

TODAY, NEXT_YEAR = "2026-11-25", "2027-06-01"


def cites(found):
    return [r["reference"] for r in found["results"]]


def build(*provisions):
    snapshot = Snapshot.load(SAMPLE)
    snapshot.provisions.extend(provisions)
    snapshot.sha256 = "f" * 64
    store.build_index(snapshot, "handbook_sample")


def rule(number, size=100, subheading=None, kind="R"):
    ref = f"XBIG 1.1.{number}{kind}"
    text = f"({number}) " + "word " * (size // 5)
    return Provision(ref=ref, provision_type=kind, subheading=subheading, text=text.strip())


# --- search_fca_handbook -------------------------------------------------------------------------------------------


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
    assert expected in cites(search_fca_handbook(query, top_k=3, as_of=TODAY))


def test_search_by_reference_puts_the_provision_first(index):
    assert cites(search_fca_handbook("XSYS 4.2.1R", as_of=TODAY))[0] == "XSYS 4.2.1R"


def test_search_results_say_what_they_are(index):
    result = next(r for r in search_fca_handbook("XSYS 4.1.2G", as_of=TODAY)["results"] if r["reference"] == "XSYS 4.1.2G")
    assert (result["binding"], result["section"], result["source"]) == (False, "XSYS 4.1", "fictional-sample")
    assert result["title"].startswith("FICTIONAL SAMPLE") and result["title"].endswith("XSYS 4.1 Testing")
    assert result["snapshot_date"] == "2026-11-25"


def test_search_returns_snippets(empty_index):
    build(rule(1, size=3000))
    result = search_fca_handbook("XBIG 1.1.1R", as_of=TODAY)["results"][0]
    assert len(result["text"]) < 320 and result["text"].endswith("[...]")


def test_a_short_part_of_a_long_provision_is_marked_as_cut(empty_index):
    tail = "Zebras are exempt."
    build(Provision(ref="XBIG 1.1.1R", provision_type="R", text="word " * 299 + "word\n" + tail))
    result = search_fca_handbook("zebras exempt", as_of=TODAY)["results"][0]
    assert (result["reference"], result["text"]) == ("XBIG 1.1.1R", f"{tail} [...]")


def test_a_short_whole_provision_is_not_marked_as_cut(index):
    result = search_fca_handbook("approved by a person other than", top_k=1, as_of=TODAY)["results"][0]
    assert result["reference"] == "XSYS 4.2.1R" and result["text"].endswith("made it.")


@pytest.mark.parametrize(
    ("scope", "books", "missing"),
    [
        (["xprn"], {"XPRN"}, []),
        (["XSYS 4"], {"XSYS"}, []),
        (["xsys 4.1"], {"XSYS"}, []),
        (["XPRN", "CONC"], {"XPRN"}, ["CONC"]),
        (["CONC", "PRIN 2A", "Consumer Credit Act 1974"], set(), ["CONC", "PRIN 2A", "CONSUMER CREDIT ACT 1974"]),
    ],
)
def test_search_scope(index, scope, books, missing):
    found = search_fca_handbook("customer software change", sourcebooks=scope, as_of=TODAY)
    assert {r.split(" ")[0] for r in cites(found)} == books and found["not_in_index"] == missing
    if scope == ["XSYS 4"]:
        assert all(r.startswith("XSYS 4.") for r in cites(found))
    if scope == ["xsys 4.1"]:
        assert all(r.startswith("XSYS 4.1.") for r in cites(found))


# --- get_fca_section -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("reference", ["XSYS 4.1.2G", "XSYS 4.1", "xsys 4.1.1 r"])
def test_section_is_whole_and_in_order(index, reference):
    found = get_fca_section(reference, as_of=TODAY)
    assert cites(found) == ["XSYS 4.1.1R", "XSYS 4.1.2G", "XSYS 4.1.3R"]
    assert (found["complete"], found["mode"], found["outline"]) == (True, "full", [])


def test_section_gives_definitions_and_links_outside_it(index):
    found = get_fca_section("XSYS 4.1.3R", as_of=TODAY)
    assert {"important business service", "emergency change"} <= set(found["definitions"])
    assert found["links"] == ["XSYS 4.2.1R"]


def test_section_uses_the_version_in_force(index):
    for day, years in ((TODAY, "three"), ("2026-12-31", "three"), ("2027-01-01", "five"), (NEXT_YEAR, "five")):
        found = get_fca_section("XSYS 5.1.1R", as_of=day)
        assert cites(found) == ["XSYS 5.1.1R", "XSYS 5.1.2R"] and years in found["results"][0]["text"]


def test_section_follows_handbook_order_not_number_order(empty_index):
    build(rule("2"), rule("10"), rule("2A"), rule("3"))
    assert cites(get_fca_section("XBIG 1.1", as_of=TODAY)) == ["XBIG 1.1.2R", "XBIG 1.1.10R", "XBIG 1.1.2AR", "XBIG 1.1.3R"]


def test_long_section_with_subheadings_returns_the_group(empty_index, monkeypatch):
    monkeypatch.setattr(handbook, "SECTION_LIMIT", 450)
    build(*(rule(n, subheading="Alpha" if n <= 3 else "Beta") for n in range(1, 7)))
    found = get_fca_section("XBIG 1.1.5R", as_of=TODAY)
    assert cites(found) == ["XBIG 1.1.4R", "XBIG 1.1.5R", "XBIG 1.1.6R"]
    assert (found["mode"], found["complete"]) == ("group", False)
    assert [line.split(":")[0] for line in found["outline"]] == ["XBIG 1.1.1R", "XBIG 1.1.2R", "XBIG 1.1.3R"]
    assert "Alpha" in found["outline"][0]


def test_long_group_returns_neighbours_within_it(empty_index, monkeypatch):
    monkeypatch.setattr(handbook, "SECTION_LIMIT", 250)
    build(*(rule(n, subheading="Alpha" if n <= 2 else "Beta") for n in range(1, 8)))
    found = get_fca_section("XBIG 1.1.3R", as_of=TODAY)
    assert found["mode"] == "neighbours" and cites(found) == ["XBIG 1.1.3R", "XBIG 1.1.4R"]


def test_long_section_without_subheadings_returns_neighbours(empty_index, monkeypatch):
    monkeypatch.setattr(handbook, "SECTION_LIMIT", 350)
    build(*(rule(n) for n in range(1, 10)))
    assert cites(get_fca_section("XBIG 1.1.5R", as_of=TODAY)) == ["XBIG 1.1.4R", "XBIG 1.1.5R", "XBIG 1.1.6R"]
    assert cites(get_fca_section("XBIG 1.1.1R", as_of=TODAY))[0] == "XBIG 1.1.1R"
    found = get_fca_section("XBIG 1.1.9R", as_of=TODAY)
    assert cites(found)[-1] == "XBIG 1.1.9R" and len(found["outline"]) + len(found["results"]) == 9


def test_a_provision_over_the_limit_is_never_cut(empty_index, monkeypatch):
    monkeypatch.setattr(handbook, "SECTION_LIMIT", 200)
    big = rule(2, size=5000)
    build(rule(1), big, rule(3))
    found = get_fca_section("XBIG 1.1.2R", as_of=TODAY)
    assert cites(found) == ["XBIG 1.1.2R"] and found["results"][0]["text"] == big.text


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        ("XSYS 4.1.9R", "not in force"),
        ("XSYS 4.1.1G", "not in force"),
        ("CONC 4.2.5R", "not in force"),
        ("XSYS 4", "XSYS 4.1, XSYS 4.2"),
        ("the testing rules", "not a Handbook reference"),
    ],
)
def test_section_refuses_what_it_does_not_have(index, reference, message):
    with pytest.raises(ValueError, match=message):
        get_fca_section(reference, as_of=TODAY)


LETTERED = ("XBIG 6.1.1R", "XBIG 6.1G.1R", "XBIG 5.1.1R", "XBIG 5C.1.1R", "XBIG 7 Annex 1.1R", "XBIG 7 Annex 2R")


@pytest.mark.parametrize(
    ("reference", "section", "provisions"),
    [
        ("XBIG 6.1G", "XBIG 6.1G", ["XBIG 6.1G.1R"]),
        ("XBIG 6.1", "XBIG 6.1", ["XBIG 6.1.1R"]),
        ("XBIG 7 Annex 1R", "XBIG 7 ANNEX 1", ["XBIG 7 Annex 1.1R"]),
        ("XBIG 7 Annex 2R", "XBIG 7 ANNEX 2", ["XBIG 7 Annex 2R"]),
    ],
)
def test_a_section_number_may_end_in_a_type_letter(empty_index, reference, section, provisions):
    build(*(Provision(ref=ref, provision_type="R", text="A firm must keep records.") for ref in LETTERED))
    found = get_fca_section(reference, as_of=TODAY)
    assert (found["section"], cites(found)) == (section, provisions)


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        ("XBIG 5C", "Sections in XBIG 5C: XBIG 5C.1."),
        ("XBIG 6.1D", "not in force"),  # no such section, and it is not XBIG 6.1
        ("XBIG 7 Annex 1G", "not in force"),  # the annex holds only rules
    ],
)
def test_a_lettered_reference_never_gives_another_section(empty_index, reference, message):
    build(*(Provision(ref=ref, provision_type="R", text="A firm must keep records.") for ref in LETTERED))
    with pytest.raises(ValueError, match=message):
        get_fca_section(reference, as_of=TODAY)


def test_a_rule_not_yet_in_force_is_refused(index):
    with pytest.raises(ValueError, match="not in force"):
        get_fca_section("XSYS 5.1.1R", as_of="2024-12-31")


# --- check_fca_citation ---------------------------------------------------------------------------------------------


def test_check_citation(index):
    good = check_fca_citation("XSYS 4.2.1R", "approved by a person other than the person who made it", TODAY)
    assert (good["ok"], good["binding"], good["source"]) == (True, True, "fictional-sample")
    guidance = check_fca_citation("XSYS 4.1.2G", "should consider automated regression tests", TODAY)
    assert (guidance["ok"], guidance["binding"]) == (True, False)
    for reference, quote in (
        ("XSYS 4.2.1R", "approved by two people every time"),
        ("XSYS 4.2.9R", "approved by a person other than"),
        ("XSYS 4.2.1R", "must"),
        ("XSYS 4.2.1R", "  "),
        ("XSYS 4.2.2G", "approved by a person other than the person who made it"),
    ):
        assert check_fca_citation(reference, quote, TODAY)["ok"] is False


# --- the index -----------------------------------------------------------------------------------------------------


def test_missing_index_bad_dates_and_wrong_model_raise(empty_index, monkeypatch):
    for call in (
        lambda: search_fca_handbook("x"),
        lambda: get_fca_section("XSYS 4.1.1R"),
        lambda: check_fca_citation("XSYS 4.1.1R", "x"),
    ):
        with pytest.raises(store.IndexNotReady, match="Run scripts/ingest.py"):
            call()
    with pytest.raises(ValueError):
        get_fca_section("XSYS 4.1.1R", as_of="tomorrow")
    store.build_index(Snapshot.load(SAMPLE), "handbook_sample")
    monkeypatch.setenv("EMBEDDING_MODEL", "openai:other")  # refused before any request is made
    with pytest.raises(store.IndexNotReady, match="built with hash"):
        search_fca_handbook("audit", as_of=TODAY)


def test_parallel_searches(index):
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: search_fca_handbook("audit logs", as_of=TODAY), range(40)))
    assert all(r == results[0] for r in results)


def test_index_survives_reopening(index):
    store.close()
    assert cites(get_fca_section("XSYS 4.1.1R", as_of=TODAY))[0] == "XSYS 4.1.1R"


def _changed_snapshot(tmp_path):
    folder = shutil.copytree(SAMPLE, tmp_path / "new")
    rows = [json.loads(line) for line in (folder / "provisions.jsonl").read_text().splitlines()]
    rows[0]["text"] += " Changed."
    (folder / "provisions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return Snapshot.load(folder)


def test_refresh_moves_the_alias_and_repeat_is_a_no_op(index, tmp_path):
    assert store.build_index(Snapshot.load(SAMPLE), "handbook_sample") == index
    assert store.build_index(_changed_snapshot(tmp_path), "handbook_sample") != index
    assert "Changed." in store.get("XSYS 4.1.1R", date.fromisoformat(TODAY))["text"]


def test_failed_refresh_leaves_the_old_index_live(index, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_payload", lambda *a: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        store.build_index(_changed_snapshot(tmp_path), "handbook_sample")
    assert "Changed." not in store.get("XSYS 4.1.1R", date.fromisoformat(TODAY))["text"]


def test_long_provision_is_split_for_search_and_rejoined_for_reading(empty_index):
    text = "\n".join(f"({n}) the firm must keep record number {n} safely." for n in range(400))
    build(Provision(ref="XBIG 9.1.1R", provision_type="R", text=text))
    assert get_fca_section("XBIG 9.1.1R", as_of=TODAY)["results"][0]["text"] == text
    assert cites(search_fca_handbook("record number 399", as_of=TODAY)).count("XBIG 9.1.1R") == 1


def test_split_never_exceeds_the_limit():
    parts = store.split_text("word " * 5000, limit=1000)
    assert len(parts) > 1 and all(len(p) <= 1000 for p in parts)


def test_duplicate_provisions_are_rejected(tmp_path):
    folder = shutil.copytree(SAMPLE, tmp_path / "dup")
    path = folder / "provisions.jsonl"
    path.write_text(path.read_text() + path.read_text().splitlines()[0] + "\n")
    with pytest.raises(ValueError, match="twice"):
        Snapshot.load(folder)
