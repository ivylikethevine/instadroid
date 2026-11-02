"""JSON Feed (/instagram.json, /stories.json) and the posts feeds' kind, since and q filters."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from shared.jsonvalues import JSON

from tests.feedclient import TWO_POSTS, make_app
from tests.support import json_at, json_object

SEED_EXTRA: str = """
INSERT INTO posts VALUES ('CAR', 'third', 'carousel', '3 days ago', 'Sale: 100% off_today', NULL,
    '2026-09-06T08:00:00+00:00', 'h3', 'https://www.instagram.com/p/CAR/', NULL,
    '2026-09-06T07:00:00+00:00');
"""


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return make_app(tmp_path, monkeypatch, TWO_POSTS + SEED_EXTRA)


def _items(r: Response) -> list[JSON]:
    items: JSON = json_object(r)["items"]
    assert isinstance(items, list)
    return items


def _authors(client: TestClient, path: str = "/instagram.json", **params: str) -> list[JSON]:
    r: Response = client.get(path, params=params)
    assert r.status_code == 200, r.text
    names: list[JSON] = []
    item: JSON
    for item in _items(r):
        authors: JSON = json_at(item, "authors")
        assert isinstance(authors, list)
        names.append(json_at(authors[0], "name"))
    return names


def test_json_feed_carries_the_atom_feeds_entries(client: TestClient) -> None:
    r: Response = client.get("/instagram.json")
    assert r.headers["content-type"] == "application/feed+json"
    body: dict[str, JSON] = json_object(r)
    assert body["version"] == "https://jsonfeed.org/version/1.1"
    assert body["title"] == "Instagram — Following"
    assert body["feed_url"] == "http://feed.test/instagram.json"
    first: JSON = _items(r)[0]
    assert json_at(first, "id") == "http://feed.test/post/ABC"
    assert json_at(first, "url") == "https://www.instagram.com/p/ABC/"
    assert json_at(first, "title") == "someone: Hi <there>"
    assert json_at(first, "date_published") == "2026-09-08T09:00:00+00:00"
    assert json_at(first, "date_modified") == "2026-09-08T08:00:00+00:00"
    assert json_at(first, "image") == "http://feed.test/media/ABC.jpg"
    content: JSON = json_at(first, "content_html")
    assert isinstance(content, str) and "&lt;there&gt;" in content
    assert json_at(first, "authors") == [{"name": "someone", "url": "https://www.instagram.com/someone/"}]
    assert client.get("/instagram.json", headers={"if-none-match": r.headers["etag"]}).status_code == 304
    assert r.headers["etag"] != client.get("/instagram.xml").headers["etag"]


def test_json_feed_for_one_account_links_its_profile(client: TestClient) -> None:
    body: dict[str, JSON] = json_object(client.get("/instagram.json", params={"user": "other"}))
    assert body["home_page_url"] == "https://www.instagram.com/other/"
    assert body["feed_url"] == "http://feed.test/instagram.json?user=other"


def test_json_feed_names_an_avatar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seed: str = TWO_POSTS + (
        "CREATE TABLE accounts (username TEXT PRIMARY KEY, account_id TEXT, avatar_file TEXT,"
        " avatar_updated_at TEXT); INSERT INTO accounts VALUES ('someone', NULL, 'avatars/someone.webp', NULL);"
    )
    item: JSON = _items(make_app(tmp_path, monkeypatch, seed).get("/instagram.json"))[0]
    assert json_at(item, "authors") == [
        {
            "name": "someone",
            "url": "https://www.instagram.com/someone/",
            "avatar": "http://feed.test/media/avatars/someone.webp",
        }
    ]


def test_stories_json_feed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seed: str = """
    CREATE TABLE stories (id TEXT PRIMARY KEY, username TEXT, media_file TEXT, kind TEXT,
        posted_date TEXT, scraped_at TEXT);
    INSERT INTO stories VALUES ('s1', 'alice', 'stories/s1.webp', 'story', '1h', '2026-09-08T08:00:00+00:00');
    """
    r: Response = make_app(tmp_path, monkeypatch, seed).get("/stories.json")
    assert json_object(r)["feed_url"] == "http://feed.test/stories.json"
    item: JSON = _items(r)[0]
    assert json_at(item, "title") == "alice's story"
    assert json_at(item, "image") == "http://feed.test/media/stories/s1.webp"
    assert json_at(item, "date_published") == "2026-09-08T08:00:00+00:00"


@pytest.mark.parametrize(
    ("kind", "authors"),
    [
        ("photo", ["someone"]),
        ("reel", ["other"]),
        ("video", ["other"]),
        ("Carousel, photo", ["someone", "third"]),
        ("", ["someone", "other", "third"]),
    ],
)
def test_kind_filter(client: TestClient, kind: str, authors: list[str]) -> None:
    assert _authors(client, kind=kind) == authors
    assert client.get("/instagram.xml", params={"kind": kind}).text.count("<entry>") == len(authors)


def test_an_unknown_kind_is_refused(client: TestClient) -> None:
    r: Response = client.get("/instagram.xml", params={"kind": "photo,story"})
    assert r.status_code == 422 and "unknown kind story" in r.text


def test_since_filter_reads_the_posted_time_in_the_tz_zone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    utc: TestClient = make_app(tmp_path, monkeypatch, TWO_POSTS + SEED_EXTRA)
    assert _authors(utc, since="2026-09-07") == ["someone", "other"]
    assert _authors(utc, since="2026-09-08T09:00:00+00:00") == ["someone"]
    assert _authors(utc, since="2026-09-08T09:00:01Z") == []
    assert utc.get("/instagram.xml", params={"since": "last week"}).status_code == 422
    (tmp_path / "posts.sqlite").unlink()
    pacific: TestClient = make_app(tmp_path, monkeypatch, TWO_POSTS + SEED_EXTRA, tz="America/Los_Angeles")
    # A time without an offset is in TZ's zone: 08:00 is before "other"'s 09:00 UTC, 08:00 PDT after it.
    assert _authors(utc, since="2026-09-07T08:00") == ["someone", "other"]
    assert _authors(pacific, since="2026-09-07T08:00") == ["someone"]


def test_q_filter_matches_caption_text_literally(client: TestClient) -> None:
    assert _authors(client, q="THERE") == ["someone"]
    assert _authors(client, q="100% off_") == ["third"]
    assert _authors(client, q="%") == ["third"]  # a literal percent sign, not a wildcard
    assert _authors(client, q="0_o") == []


def test_filters_shape_the_self_link_and_the_etag(client: TestClient) -> None:
    body: str = client.get("/instagram.xml", params={"kind": "photo", "q": "a b"}).text
    assert 'href="http://feed.test/instagram.xml?kind=photo&amp;q=a%20b" rel="self"' in body
    plain: str = client.get("/instagram.xml").headers["etag"]
    assert client.get("/instagram.xml", params={"kind": "photo"}).headers["etag"] != plain


def test_filters_with_no_posts_table_return_an_empty_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch, seed="")
    assert (
        _items(client.get("/instagram.json", params={"kind": "photo", "since": "2026-01-01", "q": "x"})) == []
    )
