"""/browse over a database with the real schema: the account list, timelines, paging, search and auth."""

import re
import sqlite3
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from tests.feedclient import make_app

TOKEN: str = "s3cret-token"


def _post(
    con: sqlite3.Connection,
    post_id: str,
    username: str,
    caption: str | None,
    day: int = 1,
    media: str | None = None,
) -> None:
    at: str = f"2026-09-{day:02d}T12:00:00+00:00"
    con.execute(
        "INSERT INTO posts (id, username, kind, caption, media_file, scraped_at, posted_at)"
        " VALUES (?,?,'photo',?,?,?,?)",
        (post_id, username, caption, media, at, at),
    )
    con.commit()


@pytest.fixture
def client(con: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return make_app(tmp_path, monkeypatch, seed="")


def _older_link(r: Response) -> str:
    m: re.Match[str] | None = re.search(r'<a href="([^"]+)" rel="next">Older</a>', r.text)
    assert m, r.text
    return unescape(m[1])


def test_the_account_list_links_each_timeline(con: sqlite3.Connection, client: TestClient) -> None:
    _post(con, "a1", "alice", "one")
    _post(con, "a2", "alice", "two", day=3)
    _post(con, "b1", "b&b", "three")
    r: Response = client.get("/browse")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert '<a href="/browse?user=alice">alice</a></td><td>2</td><td>2026-09-03' in r.text
    assert '<a href="/browse?user=b%26b">b&amp;b</a>' in r.text


def test_the_account_list_shows_an_account_without_a_date(
    con: sqlite3.Connection, client: TestClient
) -> None:
    con.execute("INSERT INTO posts (id, username, scraped_at) VALUES ('x', 'nodate', 'not a date')")
    con.commit()
    assert "<td>—</td>" in client.get("/browse").text


def test_an_empty_database_says_so(client: TestClient) -> None:
    assert '<td colspan="3">none</td>' in client.get("/browse").text
    assert "No posts." in client.get("/browse", params={"user": "nobody"}).text


def test_before_the_database_or_the_index_exists_it_still_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)  # a legacy schema with no search index
    assert "No posts." in client.get("/browse", params={"q": "hi"}).text
    assert "someone" in client.get("/browse", params={"user": "someone"}).text


def test_a_timeline_is_one_account_newest_first(con: sqlite3.Connection, client: TestClient) -> None:
    _post(con, "old", "alice", "older words", day=1)
    _post(con, "new", "alice", "newer words", day=2)
    _post(con, "else", "bob", "not hers", day=3)
    text: str = client.get("/browse", params={"user": "alice"}).text
    assert text.index("newer words") < text.index("older words")
    assert "not hers" not in text
    assert 'rel="next"' not in text


def test_paging_walks_every_post_once_including_equal_timestamps(
    con: sqlite3.Connection, client: TestClient
) -> None:
    i: int
    for i in range(5):
        _post(con, f"p{i}", "alice", f"caption{i}x", day=1)  # the same time: the id breaks the tie
    seen: list[str] = []
    r: Response = client.get("/browse", params={"user": "alice", "limit": 2})
    assert "limit=2" in _older_link(r)
    pages: int = 1
    while 'rel="next"' in r.text:
        seen += re.findall(r"caption\dx", r.text)
        r = client.get(_older_link(r))
        pages += 1
    seen += re.findall(r"caption\dx", r.text)
    assert seen == [f"caption{i}x" for i in (4, 3, 2, 1, 0)]
    assert pages == 3


def test_the_default_page_size_leaves_limit_out_of_the_link(
    con: sqlite3.Connection, client: TestClient
) -> None:
    i: int
    for i in range(51):
        _post(con, f"p{i:02d}", "alice", "words")
    r: Response = client.get("/browse", params={"user": "alice"})
    assert r.text.count("<article>") == 50
    assert "limit" not in _older_link(r)


@pytest.mark.parametrize(
    "limit, shown",
    [(0, 1), (-5, 1), (10**30, 3)],
)
def test_limit_is_clamped(con: sqlite3.Connection, client: TestClient, limit: int, shown: int) -> None:
    i: int
    for i in range(3):
        _post(con, f"p{i}", "alice", "words")
    r: Response = client.get("/browse", params={"user": "alice", "limit": limit})
    assert r.status_code == 200
    assert r.text.count("<article>") == shown


def test_search_finds_captions_across_accounts_and_within_one(
    con: sqlite3.Connection, client: TestClient
) -> None:
    _post(con, "a", "alice", "Sunset over the bay", day=1)
    _post(con, "b", "bob", "another sunset #beach", day=2)
    _post(con, "c", "bob", "breakfast", day=3)
    text: str = client.get("/browse", params={"q": "SUNSET"}).text
    assert "Sunset over" in text and "another sunset" in text and "breakfast" not in text
    assert text.index("another sunset") < text.index("Sunset over")
    only_bob: str = client.get("/browse", params={"q": "sunset", "user": "bob"}).text
    assert "another sunset" in only_bob and "Sunset over" not in only_bob
    assert '<input type="hidden" name="user" value="bob">' in only_bob
    assert "another sunset" in client.get("/browse", params={"q": "suns beach"}).text  # prefix, and AND
    assert "No posts." in client.get("/browse", params={"q": "sunset breakfast"}).text


def test_search_results_page_with_the_query_kept(con: sqlite3.Connection, client: TestClient) -> None:
    _post(con, "a", "alice", "needle one", day=1)
    _post(con, "b", "alice", "needle two", day=2)
    _post(con, "c", "alice", "other", day=3)
    r: Response = client.get("/browse", params={"q": "needle", "limit": 1})
    assert "needle two" in r.text
    second: Response = client.get(_older_link(r))
    assert "needle one" in second.text and "needle two" not in second.text and "other" not in second.text


@pytest.mark.parametrize(
    "q",
    [
        '"',
        '"unterminated',
        "a AND",
        "OR",
        "NOT x",
        "NEAR(a b)",
        "caption:x",
        "nosuchcolumn:x",
        "*",
        "x*",
        "^a",
        "a + b",
        "(",
        ")",
        "-",
        "{a b}",
        "a\x00b",
        "\x00",
        "'; DROP TABLE posts; --",
        "!!!",
        "%ff",
        "\U0001f600",
        " ".join(["w"] * 5000),
    ],
)
def test_no_query_text_reaches_fts5_as_syntax(con: sqlite3.Connection, client: TestClient, q: str) -> None:
    _post(con, "a", "alice", "plain words")
    from feedserver import queries

    assert client.get("/browse", params={"q": q}).status_code == 200
    # all_rows() would hide an FTS5 syntax error as "no rows": run the match itself.
    query: str = queries.fts_query(q)
    if query:
        con.execute("SELECT rowid FROM posts_fts WHERE posts_fts MATCH ?", (query,)).close()


def test_fts_query_quotes_every_word_and_caps_their_number(client: TestClient) -> None:
    from feedserver import queries

    assert queries.fts_query('say "hi" NOT') == '"say"* """hi"""* "NOT"*'
    assert queries.fts_query(" \t\x00 ") == ""
    assert queries.fts_query(" ".join(["w"] * 100)).count('"w"*') == queries.MAX_SEARCH_WORDS


def test_a_syntax_word_is_searched_as_a_word(con: sqlite3.Connection, client: TestClient) -> None:
    _post(con, "a", "alice", "this is NOT a drill")
    _post(con, "b", "alice", "plain")
    text: str = client.get("/browse", params={"q": "not"}).text
    assert "a drill" in text and "plain" not in text


def test_a_whitespace_query_is_the_account_list(con: sqlite3.Connection, client: TestClient) -> None:
    _post(con, "a", "alice", "words")
    assert "<article>" not in client.get("/browse", params={"q": "   "}).text


def test_every_value_is_escaped(con: sqlite3.Connection, client: TestClient) -> None:
    evil: str = '<script>alert("x")</script>'
    _post(con, "a", evil, f"{evil} words")
    index: str = client.get("/browse").text
    timeline: str = client.get("/browse", params={"user": evil}).text
    searched: str = client.get("/browse", params={"q": evil}).text
    assert "words" in timeline
    page: str
    for page in (index, timeline, searched):
        assert "<script" not in page
    assert "&lt;script&gt;" in timeline
    assert 'value="&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;"' in searched


def test_garbage_paging_values_are_harmless(con: sqlite3.Connection, client: TestClient) -> None:
    _post(con, "a", "alice", "words")
    params: dict[str, str] = {"user": "alice", "before": "'\"<x>", "before_id": "\x00"}
    assert client.get("/browse", params=params).status_code == 200
    # One half of the cursor alone is no cursor.
    assert "words" in client.get("/browse", params={"user": "alice", "before": "0"}).text


def test_browse_needs_the_token_and_writes_none_of_it(
    con: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _post(con, "a", "alice", "needle", media="a.webp")
    _post(con, "b", "alice", "needle", day=2)
    monkeypatch.setenv("FEED_TOKEN", TOKEN)
    client: TestClient = make_app(tmp_path, monkeypatch, seed="")
    params: dict[str, str]
    for params in ({}, {"user": "alice"}, {"q": "needle"}):
        r: Response = client.get("/browse", params=params)
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == 'Basic realm="instadroid"'
    page: Response = client.get(
        "/browse",
        params={"q": "needle", "limit": 1, "user": "alice"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert page.status_code == 200 and TOKEN not in page.text
    from feedserver import auth

    assert auth.media_sig("a.webp") not in page.text  # the newest post has no image
    older: Response = client.get(_older_link(page), headers={"Authorization": f"Bearer {TOKEN}"})
    assert f"a.webp?sig={auth.media_sig('a.webp')}" in older.text and TOKEN not in older.text
