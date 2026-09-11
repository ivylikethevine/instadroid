import sqlite3
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient


def make_app(tmp_path, monkeypatch):
    db = tmp_path / "posts.sqlite"
    media = tmp_path / "media"
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("MEDIA_DIR", str(media))
    monkeypatch.setenv("PUBLIC_URL", "http://feed.test")
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE posts (id TEXT PRIMARY KEY, username TEXT, kind TEXT, posted_date TEXT, caption TEXT,"
        " media_file TEXT, scraped_at TEXT, hash TEXT, url TEXT, place TEXT, posted_at TEXT)"
    )
    # Scraped in the order a run actually finds them (newest post first), so scraped_at DESC
    # would get the order backwards; posted_at DESC must recover the true chronological order.
    con.execute(
        "INSERT INTO posts VALUES ('ABC', 'someone', 'photo', '2 days ago', 'Hi <there>', 'ABC.jpg',"
        " '2026-09-08T08:00:00+00:00', 'h1', 'https://www.instagram.com/p/ABC/', 'San Diego',"
        " '2026-09-08T09:00:00+00:00')"
    )
    con.execute(
        "INSERT INTO posts VALUES ('h2', 'other', 'video', 'August 1', '', NULL,"
        " '2026-09-08T10:00:00+00:00', 'h2', NULL, NULL, '2026-09-07T09:00:00+00:00')"
    )
    con.commit()
    con.close()
    import importlib

    import app

    importlib.reload(app)
    return TestClient(app.app)


def test_feed_lists_posts_with_permalinks_and_escaping(tmp_path, monkeypatch):
    client = make_app(tmp_path, monkeypatch)
    r = client.get("/instagram.xml")
    assert r.status_code == 200
    body = r.text
    assert 'href="https://www.instagram.com/p/ABC/"' in body
    assert 'href="https://www.instagram.com/other/"' in body  # fallback when no permalink
    assert "http://feed.test/media/ABC.jpg" in body
    assert "&lt;there&gt;" in body and "<there>" not in body
    assert "San Diego" in body
    assert body.index("someone") < body.index("other")  # posted_at order, not scrape order


def test_feed_entries_expose_both_posted_and_saved_dates_for_sorting(tmp_path, monkeypatch):
    client = make_app(tmp_path, monkeypatch)
    body = client.get("/instagram.xml").text
    # Machine-sortable Atom fields (posted_at -> published, scraped_at -> updated)...
    assert "<published>2026-09-08T09:00:00+00:00</published>" in body
    assert "<updated>2026-09-08T08:00:00+00:00</updated>" in body
    # ...and spelled out in the human-readable content too, so sorting-by-eye works in any reader
    # that doesn't surface <published>/<updated>.
    assert "Posted 2 days ago (2026-09-08 09:00 UTC)" in body
    assert "saved 2026-09-08 08:00 UTC" in body


def test_user_filter_and_users_endpoint(tmp_path, monkeypatch):
    client = make_app(tmp_path, monkeypatch)
    assert client.get("/users").json() == ["other", "someone"]
    body = client.get("/instagram.xml", params={"user": "other"}).text
    assert "other" in body and "someone" not in body
    assert client.get("/health").json() == {"ok": True, "posts": 2}


def test_limit_is_clamped(tmp_path, monkeypatch):
    client = make_app(tmp_path, monkeypatch)
    r = client.get("/instagram.xml", params={"limit": 999999})
    assert r.status_code == 200  # doesn't try to scan an unbounded result set


def test_conditional_get_returns_304_when_unchanged(tmp_path, monkeypatch):
    client = make_app(tmp_path, monkeypatch)
    first = client.get("/instagram.xml")
    etag = first.headers["etag"]
    second = client.get("/instagram.xml", headers={"if-none-match": etag})
    assert second.status_code == 304


def test_missing_posts_table_returns_empty_instead_of_500(tmp_path, monkeypatch):
    # e.g. feed starts before the driver's first db_init() has created the table.
    db = tmp_path / "posts.sqlite"
    sqlite3.connect(db).close()  # empty file, no table
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("PUBLIC_URL", "http://feed.test")
    import importlib

    import app

    importlib.reload(app)
    client = TestClient(app.app)

    assert client.get("/instagram.xml").status_code == 200
    assert client.get("/users").json() == []
    assert client.get("/health").json() == {"ok": True, "posts": 0}


def test_etag_changes_when_a_row_is_merged_in_place(tmp_path, monkeypatch):
    # A driver-side merge (e.g. a placeholder caption replaced once the real one renders) updates
    # an existing row without changing its scraped_at or the post count — updated_at is what the
    # ETag must key off, or FreshRSS keeps getting a 304 with the stale caption.
    db = tmp_path / "posts.sqlite"
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("PUBLIC_URL", "http://feed.test")
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE posts (id TEXT PRIMARY KEY, username TEXT, kind TEXT, posted_date TEXT,"
        " caption TEXT, media_file TEXT, scraped_at TEXT, hash TEXT, url TEXT, place TEXT,"
        " posted_at TEXT, updated_at TEXT)"
    )
    con.execute(
        "INSERT INTO posts VALUES ('ABC', 'someone', 'carousel', '2 days ago',"
        " 'Photo 1 of 2 by Someone, 5 likes', NULL, '2026-09-08T08:00:00+00:00', 'h1', NULL, NULL,"
        " '2026-09-08T09:00:00+00:00', '2026-09-08T08:00:00+00:00')"
    )
    con.commit()
    con.close()
    import importlib

    import app

    importlib.reload(app)
    client = TestClient(app.app)

    first = client.get("/instagram.xml")
    assert "Photo 1 of 2 by Someone" in first.text
    etag = first.headers["etag"]

    # Simulate the merge: real caption in, updated_at bumped, scraped_at/count untouched.
    con = sqlite3.connect(db)
    con.execute(
        "UPDATE posts SET caption = 'The real caption', updated_at = '2026-09-08T12:00:00+00:00'"
        " WHERE id = 'ABC'"
    )
    con.commit()
    con.close()

    second = client.get("/instagram.xml", headers={"if-none-match": etag})
    assert second.status_code == 200
    assert "The real caption" in second.text
    assert second.headers["etag"] != etag


def test_status_page_with_no_runs_yet(tmp_path, monkeypatch):
    client = make_app(tmp_path, monkeypatch)
    r = client.get("/status")
    assert r.status_code == 200
    assert "No scrape runs recorded yet" in r.text
    assert "no successful run yet" in r.text


def test_status_page_shows_latest_ok_run_and_device(tmp_path, monkeypatch):
    db = tmp_path / "posts.sqlite"
    client = make_app(tmp_path, monkeypatch)
    con = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
            new_posts INTEGER, error TEXT, android_release TEXT, android_sdk TEXT, device_product TEXT
        )"""
    )
    now = datetime.now(UTC)
    con.execute(
        "INSERT INTO runs (started_at, finished_at, new_posts, error, android_release, android_sdk,"
        " device_product) VALUES (?,?,?,?,?,?,?)",
        (
            (now - timedelta(minutes=2)).isoformat(),
            now.isoformat(),
            2,
            None,
            "13",
            "33",
            "redroid_x86_64",
        ),
    )
    con.commit()
    con.close()

    body = client.get("/status").text
    assert "Android 13" in body
    assert "API 33" in body
    assert "redroid_x86_64" in body
    assert "OK" in body
    assert "2m 0s" in body
    assert "2 new post(s)" in body
    assert "someone" in body and "other" in body  # per-account totals from the seeded posts


def test_status_page_flags_an_error_run(tmp_path, monkeypatch):
    db = tmp_path / "posts.sqlite"
    client = make_app(tmp_path, monkeypatch)
    con = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
            new_posts INTEGER, error TEXT, android_release TEXT, android_sdk TEXT, device_product TEXT
        )"""
    )
    con.execute(
        "INSERT INTO runs (started_at, finished_at, new_posts, error) VALUES (?,?,?,?)",
        ("2026-09-08T08:00:00+00:00", "2026-09-08T08:00:30+00:00", 0, "RuntimeError('login failed')"),
    )
    con.commit()
    con.close()

    body = client.get("/status").text
    assert "ERROR" in body
    assert "login failed" in body


def test_short_error_truncates_multiline_stack_traces(tmp_path, monkeypatch):
    make_app(tmp_path, monkeypatch)
    import app

    assert app._short_error("RuntimeError('simple')") == "RuntimeError('simple')"
    multiline = "LaunchUiAutomationError('boom', 'a huge\nmulti-line\njava stack trace')"
    result = app._short_error(multiline)
    assert "\n" not in result
    assert result.startswith("LaunchUiAutomationError")
    long_one_liner = "x" * 200
    assert app._short_error(long_one_liner) == "x" * 139 + "…"


def test_dt_handles_naive_and_malformed_timestamps(tmp_path, monkeypatch):
    make_app(tmp_path, monkeypatch)  # ensures app is importable with env set
    import app

    assert app._dt(None) is None
    assert app._dt("") is None
    assert app._dt("not-a-timestamp") is None
    naive = app._dt("2026-09-08T10:00:00")
    assert naive is not None and naive.tzinfo is not None  # naive input gets UTC attached
    aware = app._dt("2026-09-08T10:00:00+00:00")
    assert aware.tzinfo is not None
