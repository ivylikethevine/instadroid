"""/subscribe: the URLs to give a reader, for the address the page was opened at."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from tests.feedclient import make_app

TOKEN: str = "s3cret-token"


def test_subscribe_page_spells_the_urls_for_the_address_it_was_opened_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch, public_url=None)
    r: Response = client.get("/subscribe", headers={"host": "192.168.1.20:8000"})
    assert r.status_code == 200
    assert "<code>http://192.168.1.20:8000/instagram.xml</code>" in r.text
    assert "<code>http://192.168.1.20:8000/stories.xml</code>" in r.text
    assert "<code>http://192.168.1.20:8000/opml</code>" in r.text
    assert "Add dynamic OPML" in r.text  # where the list goes in FreshRSS, which is not "Add a feed"
    assert '<a href="/opml" download="instadroid.opml">' in r.text
    # A private address: FreshRSS 1.30+ fetches it only from its allow list.
    assert "<code>192.168.1.20:8000</code>" in r.text
    # The feeds subscribe as they are; their images need PUBLIC_URL, which the page spells out.
    assert "<code>PUBLIC_URL=http://192.168.1.20:8000</code>" in r.text
    assert "only a reader on this host" not in r.text


def test_subscribe_page_uses_public_url_when_it_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch, public_url="https://feeds.example")
    body: str = client.get("/subscribe", headers={"host": "192.168.1.20:8000"}).text
    assert "<code>https://feeds.example/opml</code>" in body
    assert "192.168.1.20" not in body
    assert "<code>feeds.example:443</code>" in body  # a name: FreshRSS decides by what it resolves to
    assert 'class="warn"' not in body


def test_subscribe_page_carries_the_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEED_TOKEN", TOKEN)
    client: TestClient = make_app(tmp_path, monkeypatch)
    assert client.get("/subscribe").status_code == 401
    body: str = client.get("/subscribe", params={"token": TOKEN}).text
    assert f"<code>http://feed.test/instagram.xml?token={TOKEN}</code>" in body
    assert f"<code>http://feed.test/opml?token={TOKEN}</code>" in body
    assert f'<a href="/opml?token={TOKEN}" download="instadroid.opml">' in body


def test_subscribe_page_warns_about_a_loopback_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch, public_url=None)
    body: str = client.get("/subscribe", headers={"host": "localhost:8000"}).text
    assert "<code>http://127.0.0.1:8000/opml</code>" in body
    assert "only a reader on this host" in body
    assert "<code>FEED_HOST=0.0.0.0</code>" in body
    assert "<code>127.0.0.1:8000</code>" in body

    # Listening on every address already: the loopback URL is still only for a reader on this host.
    monkeypatch.setenv("FEED_HOST", "0.0.0.0")
    monkeypatch.setenv("FEED_TOKEN", TOKEN)
    client = make_app(tmp_path, monkeypatch, seed="", public_url=None)  # already seeded
    body = client.get("/subscribe", params={"token": TOKEN}, headers={"host": "127.0.0.1:8000"}).text
    assert "only a reader on this host" in body
    assert "FEED_HOST=0.0.0.0" not in body


def test_subscribe_page_says_when_the_database_is_not_there_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch, seed="")  # an empty file, no table
    r: Response = client.get("/subscribe")
    assert r.status_code == 200
    assert "has not created its database yet" in r.text


@pytest.mark.parametrize(
    ("base", "entry"),
    [
        ("http://127.0.0.1:8000", "127.0.0.1:8000"),
        ("http://10.0.0.5", "10.0.0.5:80"),
        ("https://nas.lan", "nas.lan:443"),
        ("http://[fd00::1]:8000", "[fd00::1]:8000"),
        ("http://203.0.113.9:8000", "203.0.113.9:8000"),  # documentation range: not global
        ("http://8.8.8.8:8000", None),
    ],
)
def test_allow_entry_is_freshrss_s_host_and_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base: str, entry: str | None
) -> None:
    make_app(tmp_path, monkeypatch)
    from feedserver import subscribe  # the copy make_app imported

    assert subscribe.allow_entry(base) == entry
