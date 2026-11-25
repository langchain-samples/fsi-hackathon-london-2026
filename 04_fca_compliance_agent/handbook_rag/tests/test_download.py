"""The downloader and converter. The API is faked: no network, no token, and the text is made up."""

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from scripts.download import SOURCEBOOKS, default_folder, download
from utils.convert import convert, to_text
from utils.fca_api import HOST, FCAError, HandbookAPI, read_token
from utils.schema import Snapshot

DAY, TOKEN = "2026-11-25", "made-up-token"
ROOT = Path(__file__).resolve().parent.parent


def record(name, kind="Rules", content="<p>A firm must keep records.</p>", **extra):
    return {
        "ProvisionName": name, "ProvisionType": kind, "ProvisionId": "p1", "Content": content, "SubSectionName": None,
        "ProvisionDate": "2025-01-01", "SectionName": "XSYS 4.1 Testing", "ChapterName": "XSYS 4 Change",
        "SourcebookName": "XSYS Fictional", **extra,
    }  # fmt: skip


def node(kind, entity, name, *parts, deleted=None):
    return {"type": kind, "entityId": entity, "name": name, "parts": list(parts), "isDeleted": deleted}


TREE = {
    "tree": [
        node("blocks", "b", "Block", node("sourcebook", "xsys", "XSYS Fictional",
            node("chapter", "xsys4", "XSYS 4 Change", node("section", "xsys4s1", "XSYS 4.1 Testing")),
            node("chapter", "xsys5", "XSYS 5 Empty"),
            node("chapter", "xsys6", "XSYS 6 Gone", deleted=True)),
            node("sourcebook", "xprn", "XPRN Other", node("chapter", "xprn1", "XPRN 1")))
    ]
}  # fmt: skip
GLOSSARY = {"glossary": [{"Term": "firm", "GlossaryHtml": "<p>a person &amp; company</p>"},
                         {"Term": "firm", "GlossaryHtml": "<p>(in XSYS) a bank</p>"},
                         {"Term": "old", "GlossaryHtml": "<p>[deleted]</p>"}]}  # fmt: skip


class FakeAPI:
    def __init__(self, records, failures=()):
        self.records, self.failures, self.requests = records, list(failures), []

    def __call__(self, request):
        self.requests.append(request)
        if self.failures:
            return self.failures.pop(0)
        body = json.loads(request.content)
        if request.url.path == "/get_all_handbook_tree":
            return httpx.Response(200, json=TREE)
        if request.url.path == "/get_latest_glossary":
            return httpx.Response(200, json=GLOSSARY)
        if body["chapter_id"] == "xsys4":
            return httpx.Response(200, json={"provision": self.records})
        return httpx.Response(404)


def run(tmp_path, records, failures=(), fake=None):
    fake = fake or FakeAPI(records, failures)
    with HandbookAPI(TOKEN, transport=httpx.MockTransport(fake), pause=0) as api:
        download(api, tmp_path, DAY, ["XSYS"])
    return fake


def test_download_and_convert(tmp_path):
    fake = run(tmp_path, [record("XSYS 4.1.1"), record("XSYS 4.1.2", "Guidance"), record("XSYS 4.1.3", content="<p>[deleted]</p>")])
    report = convert(tmp_path, DAY, ["XSYS"])
    assert (report["provisions"], report["deleted_in_handbook"], report["could_not_read"]) == (2, 1, [])
    assert report["chapters_with_nothing_in_force"] == ["xsys5"]
    snapshot = Snapshot.load(tmp_path)
    assert [p.citation for p in snapshot.provisions] == ["XSYS 4.1.1R", "XSYS 4.1.2G"]
    assert snapshot.provisions[0].url == "https://handbook.fca.org.uk/handbook/xsys4/xsys4s1#p1"
    assert [(t.term, t.definition) for t in snapshot.glossary] == [("firm", "a person & company\n(in XSYS) a bank")]
    assert snapshot.manifest.source == "fca-handbook-api" and "Open Government Licence" in snapshot.manifest.attribution
    assert {r.url.host for r in fake.requests} == {httpx.URL(HOST).host}
    assert all(r.headers["Authorization"] == f"Bearer {TOKEN}" for r in fake.requests)


@pytest.mark.parametrize(
    ("name", "kind", "citation"),
    [
        ("XSYS 4.1.1-A", "Rules", "XSYS 4.1.1-AR"),
        ("XSYS 4.1.-2", "Guidance", "XSYS 4.1.-2G"),
        ("XSYS 4.1.8E", "Evidential Provisions", "XSYS 4.1.8EE"),
        ("XSYS 4.1.9 G", "Guidance", "XSYS 4.1.9G"),
        ("XSYS 4 Annex 1R", "Rules", "XSYS 4 Annex 1R"),
        ("XSYS App 1.1.1", "Rules", "XSYS App 1.1.1R"),
        ("XSYS 4 Annex 2 Rule 1", "Rules", "XSYS 4 Annex 2 Rule 1R"),
        ("XSYS TP 6", "", "XSYS TP 6"),
    ],
)
def test_names_the_api_uses(tmp_path, name, kind, citation):
    run(tmp_path, [record(name, kind)])
    convert(tmp_path, DAY, ["XSYS"])
    assert [p.citation for p in Snapshot.load(tmp_path).provisions] == [citation]


def test_an_interrupted_download_continues(tmp_path):
    run(tmp_path, [record("XSYS 4.1.1")])
    assert run(tmp_path, [record("XSYS 4.1.1")]).requests == []
    (tmp_path / "raw" / "chapters" / "xsys4.json").unlink()
    assert len(run(tmp_path, [record("XSYS 4.1.1")]).requests) == 1


def test_a_download_is_not_continued_on_another_day(tmp_path):
    run(tmp_path, [record("XSYS 4.1.1")])
    convert(tmp_path, DAY, ["XSYS"])
    for day_recorded in (True, False):  # an older download has no day.txt
        if not day_recorded:
            (tmp_path / "raw" / "day.txt").unlink()
        fake = FakeAPI([record("XSYS 4.1.1")])
        with HandbookAPI(TOKEN, transport=httpx.MockTransport(fake), pause=0) as api:
            with pytest.raises(FCAError, match=f"was downloaded for {DAY}, not 2026-11-28"):
                download(api, tmp_path, "2026-11-28", ["XSYS"])
        assert fake.requests == []
    with pytest.raises(ValueError, match=f"the snapshot of {DAY}"):
        convert(tmp_path, "2026-11-28", ["XSYS"])
    assert Snapshot.load(tmp_path).manifest.snapshot_date.isoformat() == DAY


def test_a_partial_download_never_replaces_a_full_one(tmp_path):
    assert default_folder(DAY, SOURCEBOOKS).name == DAY == default_folder(DAY, SOURCEBOOKS[::-1]).name
    assert default_folder(DAY, ["CONC"], ["conc5"]).name == f"{DAY}-conc-conc5"
    run(tmp_path, [record("XSYS 4.1.1")])
    convert(tmp_path, DAY, ["XSYS"])
    before = (tmp_path / "provisions.jsonl").read_bytes()
    with pytest.raises(ValueError, match="other sourcebooks or chapters"):
        convert(tmp_path, DAY, ["XSYS"], ["xsys4"])
    assert (tmp_path / "provisions.jsonl").read_bytes() == before
    assert convert(tmp_path, DAY, ["XSYS"])["provisions"] == 1


@pytest.mark.parametrize(("books", "only", "unknown"), [(["XSYS", "XSYZ"], None, "XSYZ"), (["XSYS"], ["xsys4", "xsys99"], "xsys99")])
def test_a_name_that_is_not_in_the_handbook_is_refused(tmp_path, books, only, unknown):
    fake = FakeAPI([record("XSYS 4.1.1")])
    with HandbookAPI(TOKEN, transport=httpx.MockTransport(fake), pause=0) as api:
        with pytest.raises(ValueError, match=f"not in the Handbook.*: {unknown}$"):
            download(api, tmp_path, DAY, books, only)
    assert len(fake.requests) == 1  # only the list of chapters


def test_rate_limits_and_server_errors_are_retried(tmp_path):
    run(tmp_path, [record("XSYS 4.1.1")], [httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(503)])
    assert convert(tmp_path, DAY, ["XSYS"])["provisions"] == 1


@pytest.mark.parametrize("response", [httpx.Response(401, text=TOKEN), httpx.Response(302, headers={"Location": "https://evil.example"})])
def test_refusals_and_redirects_stop_without_showing_the_token(tmp_path, response):
    fake = FakeAPI([], [response])
    with pytest.raises(FCAError) as error:
        run(tmp_path, [], fake=fake)
    assert TOKEN not in str(error.value) and len(fake.requests) == 1


def test_a_changed_format_is_refused(tmp_path):
    run(tmp_path, [record("XSYS 4.1.1"), record("Something new")])
    with pytest.raises(ValueError, match="could not be read"):
        convert(tmp_path, DAY, ["XSYS"])
    assert not (tmp_path / "provisions.jsonl").exists()


def test_a_missing_sourcebook_is_refused(tmp_path):
    run(tmp_path, [])
    with pytest.raises(ValueError, match="no provisions were found for XSYS"):
        convert(tmp_path, DAY, ["XSYS"])


def test_to_text():
    markup = "<ol><li>(1) <p>A <a href='#'>firm</a>&nbsp;must:</p><ol><li><p>(a)</p><p>test; and</p></li></ol></li></ol><table><tr><td>One</td><td>Two</td></tr></table>"
    assert to_text(markup) == "(1) A firm must:\n(a) test; and\nOne | Two".replace(" ", " ")


def test_the_token_is_never_put_in_the_environment(tmp_path):
    path = tmp_path / ".env.fca"
    path.write_text(f"FCA_HANDBOOK_API_TOKEN={TOKEN}\n")
    import os

    assert read_token(path) == TOKEN and TOKEN not in os.environ.values()
    with pytest.raises(FCAError, match="not set"):
        read_token(tmp_path / "missing")


def test_searching_never_loads_the_downloader():
    code = "import mcp_server, sys; print('utils.fca_api' in sys.modules)"
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert done.stdout.strip() == "False"
