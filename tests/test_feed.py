"""The subscription routes: /instagram.xml, /stories.xml, /users and /opml."""

import sqlite3
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from shared.timestamps import parse_iso

from tests.feedclient import make_app, write_image
from tests.support import json_body


def test_feed_lists_posts_with_permalinks_and_escaping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    r: Response = client.get("/instagram.xml")
    assert r.status_code == 200
    body: str = r.text
    assert 'href="https://www.instagram.com/p/ABC/"' in body
    assert 'href="https://www.instagram.com/other/"' in body  # fallback when no permalink
    assert "http://feed.test/media/ABC.jpg" in body
    assert "&lt;there&gt;" in body and "<there>" not in body
    assert "Anytown" in body
    assert body.index("someone") < body.index("other")  # posted_at order, not scrape order


def test_feed_entries_expose_both_posted_and_saved_dates_for_sorting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    body: str = client.get("/instagram.xml").text
    # Machine-sortable Atom fields (posted_at -> published, scraped_at -> updated)...
    assert "<published>2026-09-08T09:00:00+00:00</published>" in body
    assert "<updated>2026-09-08T08:00:00+00:00</updated>" in body
    # ...and spelled out in the human-readable content too, so sorting-by-eye works in any reader
    # that doesn't surface <published>/<updated>.
    assert "Posted 2 days ago (2026-09-08 09:00 UTC)" in body
    assert "saved 2026-09-08 08:00 UTC" in body


def test_user_filter_and_users_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    assert json_body(client.get("/users")) == ["other", "someone"]
    body: str = client.get("/instagram.xml", params={"user": "other"}).text
    assert "other" in body and "someone" not in body
    assert json_body(client.get("/health")) == {"ok": True, "posts": 2}


def test_limit_is_clamped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    r: Response = client.get("/instagram.xml", params={"limit": 999999})
    assert r.status_code == 200  # doesn't try to scan an unbounded result set


def test_conditional_get_returns_304_when_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    first: Response = client.get("/instagram.xml")
    etag: str = first.headers["etag"]
    second: Response = client.get("/instagram.xml", headers={"if-none-match": etag})
    assert second.status_code == 304


def test_per_user_etag_ignores_other_accounts_new_posts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    con: sqlite3.Connection = sqlite3.connect(tmp_path / "posts.sqlite")
    con.execute("ALTER TABLE posts ADD COLUMN updated_at TEXT")
    con.commit()
    etag: str = client.get("/instagram.xml", params={"user": "someone"}).headers["etag"]
    con.execute(
        "INSERT INTO posts (id, username, scraped_at) VALUES ('NEW', 'other', '2026-09-09T00:00:00+00:00')"
    )
    con.commit()
    con.close()
    cached: Response = client.get(
        "/instagram.xml", params={"user": "someone"}, headers={"if-none-match": etag}
    )
    assert cached.status_code == 304
    assert client.get("/instagram.xml", headers={"if-none-match": etag}).status_code == 200


def test_missing_posts_table_returns_empty_instead_of_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # e.g. feed starts before the driver's first db_init() has created the table.
    client: TestClient = make_app(tmp_path, monkeypatch, seed="")  # an empty file, no table

    assert client.get("/instagram.xml").status_code == 200
    assert json_body(client.get("/users")) == []
    assert json_body(client.get("/health")) == {"ok": True, "posts": 0}


def test_etag_changes_when_a_row_is_merged_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A driver-side merge (e.g. a placeholder caption replaced once the real one renders) updates
    # an existing row without changing its scraped_at or the post count — updated_at is what the
    # ETag must key off, or FreshRSS keeps getting a 304 with the stale caption.
    db: Path = tmp_path / "posts.sqlite"
    client: TestClient = make_app(
        tmp_path,
        monkeypatch,
        seed="""
        CREATE TABLE posts (id TEXT PRIMARY KEY, username TEXT, kind TEXT, posted_date TEXT,
            caption TEXT, media_file TEXT, scraped_at TEXT, hash TEXT, url TEXT, place TEXT,
            posted_at TEXT, updated_at TEXT);
        INSERT INTO posts VALUES ('ABC', 'someone', 'carousel', '2 days ago',
            'Photo 1 of 2 by Someone, 5 likes', NULL, '2026-09-08T08:00:00+00:00', 'h1', NULL, NULL,
            '2026-09-08T09:00:00+00:00', '2026-09-08T08:00:00+00:00');
        """,
    )

    first: Response = client.get("/instagram.xml")
    assert "Photo 1 of 2 by Someone" in first.text
    etag: str = first.headers["etag"]

    # Simulate the merge: real caption in, updated_at bumped, scraped_at/count untouched.
    con: sqlite3.Connection = sqlite3.connect(db)
    con.execute(
        "UPDATE posts SET caption = 'The real caption', updated_at = '2026-09-08T12:00:00+00:00'"
        " WHERE id = 'ABC'"
    )
    con.commit()
    con.close()

    second: Response = client.get("/instagram.xml", headers={"if-none-match": etag})
    assert second.status_code == 200
    assert "The real caption" in second.text
    assert second.headers["etag"] != etag


def test_feed_renders_extra_carousel_slides_and_avatar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(
        tmp_path,
        monkeypatch,
        seed="""
        CREATE TABLE posts (id TEXT PRIMARY KEY, username TEXT, kind TEXT, posted_date TEXT, caption TEXT,
            media_file TEXT, scraped_at TEXT, hash TEXT, url TEXT, place TEXT, posted_at TEXT);
        CREATE TABLE media (post_id TEXT, idx INTEGER, file TEXT);
        CREATE TABLE accounts (username TEXT PRIMARY KEY, avatar_file TEXT, avatar_updated_at TEXT);
        INSERT INTO posts VALUES ('C1', 'carouseler', 'carousel', '1 day ago', 'Look at these',
            'C1.jpg', '2026-09-08T08:00:00+00:00', 'h1', NULL, NULL, '2026-09-08T09:00:00+00:00');
        INSERT INTO media VALUES ('C1', 1, 'C1_1.jpg'), ('C1', 2, 'C1_2.jpg');
        INSERT INTO accounts VALUES ('carouseler', 'avatars/carouseler.jpg', '2026-09-08T08:00:00+00:00');
        """,
    )

    body: str = client.get("/instagram.xml").text
    assert "http://feed.test/media/C1.jpg" in body  # cover
    assert "http://feed.test/media/C1_1.jpg" in body
    assert "http://feed.test/media/C1_2.jpg" in body
    assert "http://feed.test/media/avatars/carouseler.jpg" in body


def test_feed_falls_back_to_cover_image_without_media_or_accounts_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)  # legacy schema: no media/accounts tables at all
    body: str = client.get("/instagram.xml").text
    assert "http://feed.test/media/ABC.jpg" in body


def test_etag_changes_when_a_carousel_slide_is_added(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db: Path = tmp_path / "posts.sqlite"
    client: TestClient = make_app(
        tmp_path,
        monkeypatch,
        seed="""
        CREATE TABLE posts (id TEXT PRIMARY KEY, username TEXT, kind TEXT, posted_date TEXT, caption TEXT,
            media_file TEXT, scraped_at TEXT, hash TEXT, url TEXT, place TEXT, posted_at TEXT, updated_at TEXT);
        CREATE TABLE media (post_id TEXT, idx INTEGER, file TEXT);
        INSERT INTO posts VALUES ('C1', 'someone', 'carousel', '1 day ago', 'cap', 'C1.jpg',
            '2026-09-08T08:00:00+00:00', 'h1', NULL, NULL, '2026-09-08T09:00:00+00:00',
            '2026-09-08T08:00:00+00:00');
        """,
    )

    first: Response = client.get("/instagram.xml")
    etag: str = first.headers["etag"]

    con: sqlite3.Connection = sqlite3.connect(db)
    con.execute("INSERT INTO media VALUES ('C1', 1, 'C1_1.jpg')")  # a slide captured on a later run
    con.commit()
    con.close()

    second: Response = client.get("/instagram.xml", headers={"if-none-match": etag})
    assert second.status_code == 200
    assert second.headers["etag"] != etag


def test_etag_changes_when_an_avatar_is_captured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db: Path = tmp_path / "posts.sqlite"
    client: TestClient = make_app(tmp_path, monkeypatch)
    con: sqlite3.Connection = sqlite3.connect(db)
    con.execute("CREATE TABLE accounts (username TEXT PRIMARY KEY, avatar_file TEXT, avatar_updated_at TEXT)")
    con.commit()
    con.close()

    first: Response = client.get("/instagram.xml")
    etag: str = first.headers["etag"]

    con = sqlite3.connect(db)
    con.execute("INSERT INTO accounts VALUES ('someone', 'avatars/someone.jpg', '2026-09-08T12:00:00+00:00')")
    con.commit()
    con.close()

    second: Response = client.get("/instagram.xml", headers={"if-none-match": etag})
    assert second.status_code == 200
    assert "avatars/someone.jpg" in second.text
    assert second.headers["etag"] != etag


def _make_stories_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, TestClient]:
    seed: str = """
    CREATE TABLE stories (id TEXT PRIMARY KEY, username TEXT, media_file TEXT, kind TEXT,
        posted_date TEXT, scraped_at TEXT);
    """
    return tmp_path / "posts.sqlite", make_app(tmp_path, monkeypatch, seed)


def test_stories_feed_lists_stored_stories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db: Path
    client: TestClient
    db, client = _make_stories_db(tmp_path, monkeypatch)
    now: datetime = datetime.now(UTC)
    con: sqlite3.Connection = sqlite3.connect(db)
    con.execute(
        "INSERT INTO stories VALUES ('s1','alice','stories/s1.jpg','story','1h',?)",
        ((now - timedelta(hours=1)).isoformat(),),
    )
    con.execute(
        "INSERT INTO stories VALUES ('s2','bob','stories/s2.jpg','story','30h',?)",
        ((now - timedelta(hours=30)).isoformat(),),
    )
    con.commit()
    con.close()

    body: str = client.get("/stories.xml").text
    assert "alice" in body
    assert "http://feed.test/media/stories/s1.jpg" in body
    assert "bob" in body  # stories no longer expire on their own schedule (see RETAIN_DAYS)


def test_stories_feed_empty_when_table_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)  # legacy schema: no stories table at all
    r: Response = client.get("/stories.xml")
    assert r.status_code == 200
    assert "<entry>" not in r.text


def test_stories_feed_conditional_get_returns_304(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _: Path
    client: TestClient
    _, client = _make_stories_db(tmp_path, monkeypatch)
    first: Response = client.get("/stories.xml")
    etag: str = first.headers["etag"]
    second: Response = client.get("/stories.xml", headers={"if-none-match": etag})
    assert second.status_code == 304


def test_stories_feed_etag_changes_when_a_story_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db: Path
    client: TestClient
    db, client = _make_stories_db(tmp_path, monkeypatch)
    now: datetime = datetime.now(UTC)
    con: sqlite3.Connection = sqlite3.connect(db)
    con.execute(
        "INSERT INTO stories VALUES ('s1','alice','stories/s1.jpg','story','1h',?)",
        ((now - timedelta(hours=1)).isoformat(),),
    )
    con.commit()
    con.close()

    first: Response = client.get("/stories.xml")
    assert "alice" in first.text
    etag: str = first.headers["etag"]

    # Simulate the retention sweep removing the story (as _prune_expired_stories() eventually does).
    con = sqlite3.connect(db)
    con.execute("DELETE FROM stories WHERE id = 's1'")
    con.commit()
    con.close()

    second: Response = client.get("/stories.xml", headers={"if-none-match": etag})
    assert second.status_code == 200
    assert "alice" not in second.text
    assert second.headers["etag"] != etag


def test_opml_lists_every_account_plus_the_aggregate_and_stories_feeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xml.etree.ElementTree as ET

    client: TestClient = make_app(tmp_path, monkeypatch)
    r: Response = client.get("/opml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/x-opml")

    root: ET.Element = ET.fromstring(r.text)
    assert root.tag == "opml"
    category: ET.Element | None = root.find("./body/outline")
    assert category is not None
    assert category.get("text") == "Instagram"
    outlines: list[ET.Element] = category.findall("outline")
    xml_urls: list[str | None] = [o.get("xmlUrl") for o in outlines]
    assert "http://feed.test/instagram.xml" in xml_urls
    assert "http://feed.test/stories.xml" in xml_urls
    assert "http://feed.test/instagram.xml?user=other" in xml_urls
    assert "http://feed.test/instagram.xml?user=someone" in xml_urls
    assert len(outlines) == 4  # aggregate + stories + 2 accounts
    assert all(o.get("type") == "rss" for o in outlines)


def test_opml_escapes_and_quotes_unusual_usernames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import xml.etree.ElementTree as ET

    client: TestClient = make_app(tmp_path, monkeypatch)
    db: Path = tmp_path / "posts.sqlite"
    con: sqlite3.Connection = sqlite3.connect(db)
    con.execute(
        "INSERT INTO posts VALUES ('w1', 'a & b', 'photo', 'now', '', NULL,"
        " '2026-09-08T08:00:00+00:00', 'h3', NULL, NULL, NULL)"
    )
    con.commit()
    con.close()

    r: Response = client.get("/opml")
    root: ET.Element = ET.fromstring(r.text)  # raises if the & wasn't escaped into valid XML
    outlines: list[ET.Element] = root.findall("./body/outline/outline")
    weird: ET.Element = next(o for o in outlines if o.get("text") == "a & b")
    assert weird.get("xmlUrl") == "http://feed.test/instagram.xml?user=a%20%26%20b"


def test_opml_without_a_posts_table_returns_an_empty_category_instead_of_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xml.etree.ElementTree as ET

    client: TestClient = make_app(tmp_path, monkeypatch, seed="")  # an empty file, no table

    r: Response = client.get("/opml")
    assert r.status_code == 200
    root: ET.Element = ET.fromstring(r.text)
    outlines: list[ET.Element] = root.findall("./body/outline/outline")
    xml_urls: set[str | None] = {o.get("xmlUrl") for o in outlines}
    assert xml_urls == {"http://feed.test/instagram.xml", "http://feed.test/stories.xml"}


def test_opml_conditional_get_returns_304_when_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    first: Response = client.get("/opml")
    etag: str = first.headers["etag"]
    second: Response = client.get("/opml", headers={"if-none-match": etag})
    assert second.status_code == 304


def test_parse_iso_handles_naive_and_malformed_timestamps() -> None:
    assert parse_iso(None) is None
    assert parse_iso("") is None
    assert parse_iso("not-a-timestamp") is None
    assert parse_iso(1757325600) is None  # not text: malformed
    naive: datetime | None = parse_iso("2026-09-08T10:00:00")
    assert naive is not None and naive.tzinfo is not None  # naive input gets UTC attached
    aware: datetime | None = parse_iso("2026-09-08T10:00:00+00:00")
    assert aware is not None and aware.tzinfo is not None


def test_feed_images_carry_their_dimensions_and_a_thumbnail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    write_image(tmp_path / "media" / "ABC.jpg", (1080, 1350))
    body: str = client.get("/instagram.xml").text
    assert (
        'src="http://feed.test/media/ABC.jpg" alt="" width="1080" height="1350"'
        ' style="max-width:100%;height:auto"'
    ) in body.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    assert '<media:thumbnail url="http://feed.test/media/ABC.jpg" height="1350" width="1080"/>' in body


def test_feed_image_without_a_file_on_disk_has_no_dimensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)  # ABC.jpg is in the DB but was never written
    body: str = (
        client.get("/instagram.xml").text.replace("&quot;", '"').replace("&gt;", ">").replace("&lt;", "<")
    )
    assert '<img src="http://feed.test/media/ABC.jpg" alt="" />' in body
    assert '<media:thumbnail url="http://feed.test/media/ABC.jpg"/>' in body


def test_feed_image_that_is_not_an_image_has_no_dimensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    (tmp_path / "media").mkdir(exist_ok=True)
    (tmp_path / "media" / "ABC.jpg").write_bytes(b"not a jpeg at all")  # truncated by a crash mid-write
    body: str = (
        client.get("/instagram.xml").text.replace("&quot;", '"').replace("&gt;", ">").replace("&lt;", "<")
    )
    assert '<img src="http://feed.test/media/ABC.jpg" alt="" />' in body
    assert '<media:thumbnail url="http://feed.test/media/ABC.jpg"/>' in body


POSTED_AT_ONLY: str = """
CREATE TABLE posts (id TEXT PRIMARY KEY, username TEXT, kind TEXT, posted_date TEXT, caption TEXT,
    media_file TEXT, scraped_at TEXT, hash TEXT, url TEXT, place TEXT, posted_at TEXT);
INSERT INTO posts VALUES ('h3', 'someone', 'photo', NULL, 'No relative date', NULL,
    '2026-09-08T08:00:00+00:00', 'h3', NULL, NULL, '2026-09-07T09:30:00+00:00');
"""


def test_feed_entry_dates_a_post_from_posted_at_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch, seed=POSTED_AT_ONLY)
    body: str = client.get("/instagram.xml").text.replace("&lt;", "<").replace("&gt;", ">")
    assert "<small>Posted 2026-09-07 09:30 UTC · saved 2026-09-08 08:00 UTC · " in body
    assert "<published>2026-09-07T09:30:00+00:00</published>" in body


def test_video_titles_get_a_play_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    body: str = client.get("/instagram.xml").text
    assert "<title>▶ other: video</title>" in body  # h2 is a video
    assert "<title>someone: Hi &lt;there&gt;</title>" in body  # photos unchanged


def test_stories_feed_images_carry_dimensions_and_a_thumbnail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db: Path
    client: TestClient
    db, client = _make_stories_db(tmp_path, monkeypatch)
    now: datetime = datetime.now(UTC)
    write_image(tmp_path / "media" / "stories" / "s1.webp", (1080, 1900))
    con: sqlite3.Connection = sqlite3.connect(db)
    con.execute(
        "INSERT INTO stories VALUES ('s1','alice','stories/s1.webp','story','1h',?)",
        ((now - timedelta(hours=1)).isoformat(),),
    )
    con.commit()
    con.close()
    body: str = client.get("/stories.xml").text
    assert 'width="1080" height="1900"' in body.replace("&quot;", '"')
    assert (
        '<media:thumbnail url="http://feed.test/media/stories/s1.webp" height="1900" width="1080"/>' in body
    )


def test_caption_mentions_and_hashtags_become_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_app(tmp_path, monkeypatch)
    from feedserver import render

    html: str = render.caption_html("Dinner with @jane.doe and @bob_99. #foodie #2024 #année\nnext line")
    assert '<a href="https://www.instagram.com/jane.doe/">@jane.doe</a>' in html
    assert '<a href="https://www.instagram.com/bob_99/">@bob_99</a>.' in html  # full stop left outside
    assert '<a href="https://www.instagram.com/explore/tags/foodie/">#foodie</a>' in html
    assert "#2024" in html and "tags/2024" not in html  # all digits isn't a hashtag
    assert '<a href="https://www.instagram.com/explore/tags/ann%C3%A9e/">#année</a>' in html
    assert html.endswith("<br/>next line")


def test_caption_links_leave_emails_urls_and_entities_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_app(tmp_path, monkeypatch)
    from feedserver import render

    html: str = render.caption_html("mail me@example.com, see example.com/#top, C# and it's <b>@x</b>")
    assert "me@example.com" in html and "instagram.com/example" not in html
    assert "tags/top" not in html and "tags/C" not in html
    assert "it&#x27;s" in html and "tags/x27" not in html  # an escaped quote is not a hashtag
    assert "&lt;b&gt;" in html and "<b>" not in html  # still escaped around a link
    assert '<a href="https://www.instagram.com/x/">@x</a>' in html
    too_long: str = "@" + "a" * 31
    assert render.caption_html(too_long) == too_long


def test_feed_entry_links_caption_mentions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client: TestClient = make_app(tmp_path, monkeypatch)
    con: sqlite3.Connection = sqlite3.connect(tmp_path / "posts.sqlite")
    con.execute("UPDATE posts SET caption = 'shot by @photog #sunset' WHERE id = 'ABC'")
    con.commit()
    con.close()
    body: str = client.get("/instagram.xml").text
    content: str = body.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    assert '<a href="https://www.instagram.com/photog/">@photog</a>' in content
    assert "<title>someone: shot by @photog #sunset</title>" in body  # titles stay plain text
