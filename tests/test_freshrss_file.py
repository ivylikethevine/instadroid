"""`python -m feedserver.freshrss`: the file whose import makes FreshRSS follow /opml."""

import runpy
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tests.feedclient import make_app

FRSS: str = "{https://freshrss.org/opml}"


def _category(xml: bytes) -> ET.Element:
    category: ET.Element | None = ET.fromstring(xml).find("./body/outline")
    assert category is not None
    return category


def test_category_file_follows_opml_and_holds_the_stories_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FEED_TOKEN", "s3cret & more")
    make_app(tmp_path, monkeypatch)
    from feedserver import freshrss  # the copy make_app imported

    xml: bytes = freshrss.category_file("http://feed.test")
    # FreshRSS's importer matches the qualified name, so the prefix has to be literally `frss`.
    assert b'xmlns:frss="https://freshrss.org/opml"' in xml
    assert b'frss:opmlUrl="http://feed.test/opml?token=s3cret%20%26%20more"' in xml
    category: ET.Element = _category(xml)
    assert category.get("text") == "Instagram"
    assert category.get(f"{FRSS}opmlUrl") == "http://feed.test/opml?token=s3cret%20%26%20more"
    # FreshRSS skips a category that holds no feed.
    feeds: list[ET.Element] = category.findall("outline")
    assert [f.get("xmlUrl") for f in feeds] == ["http://feed.test/stories.xml?token=s3cret%20%26%20more"]
    assert feeds[0].get("type") == "rss"


@pytest.mark.parametrize(
    ("public_url", "base"),
    [(None, "http://127.0.0.1:9000"), ("https://feeds.example/", "https://feeds.example")],
    ids=["loopback", "public_url"],
)
def test_run_as_a_script_it_writes_the_file_for_this_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsysbinary: pytest.CaptureFixture[bytes],
    public_url: str | None,
    base: str,
) -> None:
    monkeypatch.setenv("FEED_PORT", "9000")
    monkeypatch.setenv("OPML_FEED_TTL", "300")
    make_app(tmp_path, monkeypatch, public_url=public_url)
    exit_info: pytest.ExceptionInfo[SystemExit]
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("feedserver.freshrss", run_name="__main__")
    assert exit_info.value.code == 0
    category: ET.Element = _category(capsysbinary.readouterr().out)
    assert category.get(f"{FRSS}opmlUrl") == f"{base}/opml"
    feed: ET.Element | None = category.find("outline")
    assert feed is not None
    assert feed.get("xmlUrl") == f"{base}/stories.xml"
    assert feed.get(f"{FRSS}ttl") == "300"
