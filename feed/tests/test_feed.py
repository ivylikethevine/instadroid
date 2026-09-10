import sqlite3

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
