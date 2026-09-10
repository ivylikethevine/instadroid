"""Serves the scraped posts as Atom feeds for FreshRSS.

/instagram.xml            -> everything
/instagram.xml?user=NAME  -> one account
/media/<file>             -> cropped post images
"""

import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha1
from html import escape
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from feedgen.feed import FeedGenerator

DB_PATH = os.environ.get("DB_PATH", "/db/posts.sqlite")
MEDIA_DIR = os.environ.get("MEDIA_DIR", "/media")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")
MAX_LIMIT = 500

app = FastAPI()
Path(MEDIA_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


def _connect():
    # The compose mount is already read-only; open read-only here too so a lock held by the
    # driver's writer never blocks a request.
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def rows(user: str | None, limit: int):
    if not Path(DB_PATH).exists():
        return []
    with closing(_connect()) as con:
        con.row_factory = sqlite3.Row
        q = "SELECT * FROM posts"
        args: list = []
        if user:
            q += " WHERE username = ?"
            args.append(user)
        q += " ORDER BY COALESCE(posted_at, scraped_at) DESC LIMIT ?"
        args.append(max(1, min(limit, MAX_LIMIT)))
        return con.execute(q, args).fetchall()


def _stats():
    """(post count, latest scraped_at) - cheap aggregate used for /health and the feed's ETag,
    so a poll that hasn't seen new data doesn't cost a full row scan or feed render."""
    if not Path(DB_PATH).exists():
        return 0, ""
    with closing(_connect()) as con:
        return con.execute("SELECT COUNT(*), COALESCE(MAX(scraped_at), '') FROM posts").fetchone()


@app.get("/instagram.xml")
def feed(request: Request, user: str | None = None, limit: int = 200):
    count, latest = _stats()
    etag = f'"{sha1(f"{user}|{limit}|{count}|{latest}".encode()).hexdigest()}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    fg = FeedGenerator()
    title = f"Instagram — {user}" if user else "Instagram — Following"
    fg.id(f"{PUBLIC_URL}/instagram.xml" + (f"?user={user}" if user else ""))
    fg.title(title)
    fg.link(href=fg.id(), rel="self")
    fg.updated(datetime.now(UTC))

    for r in rows(user, limit):
        fe = fg.add_entry(order="append")
        fe.id(f"{PUBLIC_URL}/post/{r['id']}")
        caption = r["caption"] or ""
        first_line = caption.split("\n", 1)[0][:90] or r["kind"] or "post"
        fe.title(f"{r['username']}: {first_line}")
        keys = r.keys()  # sqlite3.Row has no __contains__
        url = r["url"] if "url" in keys and r["url"] else f"https://www.instagram.com/{r['username']}/"
        fe.link(href=url)
        fe.author(name=r["username"])
        fe.updated(datetime.fromisoformat(r["scraped_at"]))
        if "posted_at" in keys and r["posted_at"]:
            fe.published(datetime.fromisoformat(r["posted_at"]))
        html = ""
        if r["media_file"]:
            html += f'<p><img src="{PUBLIC_URL}/media/{r["media_file"]}" alt="" /></p>'
        html += f"<p>{escape(caption).replace(chr(10), '<br/>')}</p>"
        meta = [f"Posted {escape(r['posted_date'])}"] if r["posted_date"] else []
        if "place" in keys and r["place"]:
            meta.append(f"at {escape(r['place'])}")
        meta.append(f'<a href="{escape(url)}">open on Instagram</a>')
        html += f"<p><small>{' · '.join(meta)}</small></p>"
        fe.content(html, type="html")

    return Response(fg.atom_str(pretty=True), media_type="application/atom+xml", headers={"ETag": etag})


@app.get("/users")
def users():
    if not Path(DB_PATH).exists():
        return []
    with closing(_connect()) as con:
        return [r[0] for r in con.execute("SELECT DISTINCT username FROM posts ORDER BY username")]


@app.get("/health")
def health():
    count, _ = _stats()
    return {"ok": True, "posts": count}
