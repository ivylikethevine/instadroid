"""Serves the scraped posts as Atom feeds for FreshRSS.

  /instagram.xml            -> everything
  /instagram.xml?user=NAME  -> one account
  /media/<file>             -> cropped post images
"""
import os
import sqlite3
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from feedgen.feed import FeedGenerator

DB_PATH = os.environ.get("DB_PATH", "/db/posts.sqlite")
MEDIA_DIR = os.environ.get("MEDIA_DIR", "/media")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")

app = FastAPI()
Path(MEDIA_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


def rows(user: str | None, limit: int):
    if not Path(DB_PATH).exists():
        return []
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    q = "SELECT * FROM posts"
    args: list = []
    if user:
        q += " WHERE username = ?"
        args.append(user)
    q += " ORDER BY scraped_at DESC LIMIT ?"
    args.append(limit)
    return con.execute(q, args).fetchall()


@app.get("/instagram.xml")
def feed(user: str | None = None, limit: int = 200):
    fg = FeedGenerator()
    title = f"Instagram — {user}" if user else "Instagram — Following"
    fg.id(f"{PUBLIC_URL}/instagram.xml" + (f"?user={user}" if user else ""))
    fg.title(title)
    fg.link(href=fg.id(), rel="self")
    fg.updated(datetime.now(timezone.utc))

    for r in rows(user, limit):
        fe = fg.add_entry(order="append")
        fe.id(f"{PUBLIC_URL}/post/{r['id']}")
        caption = r["caption"] or ""
        first_line = caption.split("\n", 1)[0][:90] or r["kind"] or "post"
        fe.title(f"{r['username']}: {first_line}")
        fe.link(href=f"https://www.instagram.com/{r['username']}/")
        fe.author(name=r["username"])
        fe.updated(datetime.fromisoformat(r["scraped_at"]))
        html = ""
        if r["media_file"]:
            html += f'<p><img src="{PUBLIC_URL}/media/{r["media_file"]}" alt="" /></p>'
        html += f"<p>{escape(caption).replace(chr(10), '<br/>')}</p>"
        if r["posted_date"]:
            html += f"<p><small>Posted {escape(r['posted_date'])}</small></p>"
        fe.content(html, type="html")

    return Response(fg.atom_str(pretty=True), media_type="application/atom+xml")


@app.get("/users")
def users():
    return sorted({r["username"] for r in rows(None, 5000)})


@app.get("/health")
def health():
    return {"ok": True, "posts": len(rows(None, 100000))}
