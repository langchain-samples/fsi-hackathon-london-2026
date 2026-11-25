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
    ],
)
def test_section_of(key, section):
    assert section_of(key) == section
