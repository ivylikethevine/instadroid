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
        " media_file TEXT, scraped_at TEXT, hash TEXT, url TEXT, place TEXT)"
    )
    con.execute(
        "INSERT INTO posts VALUES ('ABC', 'someone', 'photo', '2 days ago', 'Hi <there>', 'ABC.jpg',"
        " '2026-09-08T10:00:00+00:00', 'h1', 'https://www.instagram.com/p/ABC/', 'San Diego')"
    )
    con.execute(
        "INSERT INTO posts VALUES ('h2', 'other', 'video', 'August 1', '', NULL,"
        " '2026-09-07T10:00:00+00:00', 'h2', NULL, NULL)"
    )
    con.commit()
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
    assert body.index("someone") < body.index("other")  # newest scraped first


def test_user_filter_and_users_endpoint(tmp_path, monkeypatch):
    client = make_app(tmp_path, monkeypatch)
    assert client.get("/users").json() == ["other", "someone"]
    body = client.get("/instagram.xml", params={"user": "other"}).text
    assert "other" in body and "someone" not in body
    assert client.get("/health").json() == {"ok": True, "posts": 2}
