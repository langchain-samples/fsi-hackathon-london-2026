import pytest
from utils.references import ancestors, find_references, parse_reference, section_of


@pytest.mark.parametrize(
    ("text", "key", "type_"),
    [
        ("SYSC 15A.2.1R", "SYSC 15A.2.1", "R"),
        ("sysc 15a.2.1 r", "SYSC 15A.2.1", "R"),
        ("SYSC 3.2.6AR", "SYSC 3.2.6A", "R"),
        ("SYSC 3.2.6EG", "SYSC 3.2.6E", "G"),
        ("COBS 4.5A.3UK", "COBS 4.5A.3", "UK"),
        ("COBS 4.7.-1AR", "COBS 4.7.-1A", "R"),
        ("SYSC 1 Annex 1.2R", "SYSC 1 ANNEX 1.2", "R"),
        ("PRIN 2A.2", "PRIN 2A.2", None),
        ("SYSC 4.1.1-AR", "SYSC 4.1.1-A", "R"),
        ("SYSC 4.1.-2G", "SYSC 4.1.-2", "G"),
        ("SYSC 4.1.8DCG", "SYSC 4.1.8DC", "G"),
        ("SYSC TP 6", "SYSC TP 6", None),
        ("CONC App 1.1.1R", "CONC APP 1.1.1", "R"),
        ("COBS 13 Annex 2 Rule 1R", "COBS 13 ANNEX 2 RULE 1", "R"),
        ("SYSC 1 Annex 1 Table AR", "SYSC 1 ANNEX 1 TABLE A", "R"),
        ("SYSC Sch 6.1A", "SYSC SCH 6.1A", None),
        ("SYSC 19G Annex 1R", "SYSC 19G ANNEX 1", "R"),
        ("SYSC 15A", "SYSC 15A", None),
    ],
)
def test_parse(text, key, type_):
    ref = parse_reference(text)
    assert (ref.key, ref.type) == (key, type_)


@pytest.mark.parametrize("text", ["", "SYSC", "15A.2.1R", "see SYSC 4.1.1R", "SYSC 4.1.1R and more"])
def test_not_a_reference(text):
    assert parse_reference(text) is None


def test_find_in_text():
    found = find_references("Subject to SYSC 4.1.1R, SYSC 8.1.8 R and PRIN 2A.2.8R (see SYSC 4.1.1R).")
    assert [str(r) for r in found] == ["SYSC 4.1.1R", "SYSC 8.1.8R", "PRIN 2A.2.8R"]


def test_sentence_end_is_not_part_of_the_reference():
    assert [str(r) for r in find_references("See SYSC 4.1.1R. Next.")] == ["SYSC 4.1.1R"]


def test_ancestors():
    assert ancestors("SYSC 15A.2.1") == ["SYSC 15A.2", "SYSC 15A"]


@pytest.mark.parametrize(
    ("key", "section"),
    [
        ("SYSC 15A.2.1", "SYSC 15A.2"),
        ("SYSC 15A.2", "SYSC 15A.2"),
        ("COBS 4.7.-1A", "COBS 4.7"),
        ("SYSC 1 ANNEX 1.2", "SYSC 1 ANNEX 1"),
        ("SYSC 4", "SYSC 4"),
        ("SYSC SCH 6.1A", "SYSC SCH 6"),
        ("SYSC TP 6", "SYSC TP 6"),
        ("CONC APP 1.1.1", "CONC APP 1.1"),
        ("COBS 13 ANNEX 2 RULE 1", "COBS 13 ANNEX 2"),
    ],
)
def test_section_of(key, section):
    assert section_of(key) == section


def test_references_are_written_as_the_handbook_writes_them():
    assert [str(r) for r in find_references("See SYSC 22 Annex 1.1R and PRIN Sch 1.1.")] == ["SYSC 22 Annex 1.1R", "PRIN Sch 1.1"]
