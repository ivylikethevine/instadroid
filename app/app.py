"""Serves the scraped posts as Atom feeds for FreshRSS.

/instagram.xml            -> everything
/instagram.xml?user=NAME  -> one account
/media/<file>             -> cropped post images
/status                   -> plain-HTML health/status page
"""

import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from hashlib import sha1
from html import escape
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
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


def _dt(value):
    """Parse a stored ISO timestamp, tolerant of a missing/malformed value and of a naive one
    (feedgen rejects those) — returns None rather than raising, so one bad row can't 500 the
    whole feed."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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
        try:
            return con.execute(q, args).fetchall()
        except sqlite3.OperationalError:
            return []  # e.g. the driver hasn't run db_init() yet and the table doesn't exist


def _media_rows(post_ids: list) -> dict:
    """{post_id: [extra slide filenames, in order]} for the given posts. Empty (not an error) if
    the media table doesn't exist yet — a post's cover in media_file still renders on its own."""
    if not post_ids:
        return {}
    with closing(_connect()) as con:
        placeholders = ",".join("?" * len(post_ids))
        try:
            out: dict[str, list] = {}
            for post_id, file in con.execute(
                f"SELECT post_id, file FROM media WHERE post_id IN ({placeholders}) ORDER BY post_id, idx",
                post_ids,
            ):
                out.setdefault(post_id, []).append(file)
            return out
        except sqlite3.OperationalError:
            return {}


def _avatar_files() -> dict:
    """{username: avatar_file}. Empty if the accounts table doesn't exist yet or has no avatars."""
    with closing(_connect()) as con:
        try:
            return dict(
                con.execute("SELECT username, avatar_file FROM accounts WHERE avatar_file IS NOT NULL")
            )
        except sqlite3.OperationalError:
            return {}


def _media_accounts_signal():
    """(media row count, latest avatar refresh) - extra ETag input alongside _stats() so a newly
    captured carousel slide or avatar invalidates a cached feed even when the post count and
    updated_at haven't moved. Same defensive OperationalError handling as _stats()."""
    if not Path(DB_PATH).exists():
        return 0, ""
    with closing(_connect()) as con:
        try:
            media_count = con.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        except sqlite3.OperationalError:
            media_count = 0
        try:
            latest_avatar = con.execute(
                "SELECT COALESCE(MAX(avatar_updated_at), '') FROM accounts"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            latest_avatar = ""
        return media_count, latest_avatar


def _stats():
    """(post count, latest change) - cheap aggregate used for /health and the feed's ETag, so a
    poll that hasn't seen new data doesn't cost a full row scan or feed render. updated_at also
    moves when an existing row is merged/edited in place (e.g. a placeholder caption gets
    replaced once the real one renders), which scraped_at alone would miss."""
    if not Path(DB_PATH).exists():
        return 0, ""
    with closing(_connect()) as con:
        try:
            return con.execute(
                "SELECT COUNT(*), COALESCE(MAX(COALESCE(updated_at, scraped_at)), '') FROM posts"
            ).fetchone()
        except sqlite3.OperationalError:
            pass  # e.g. driver hasn't added the updated_at column to this table yet
        try:
            return con.execute("SELECT COUNT(*), COALESCE(MAX(scraped_at), '') FROM posts").fetchone()
        except sqlite3.OperationalError:
            return 0, ""  # e.g. the driver hasn't run db_init() yet and the table doesn't exist


@app.get("/instagram.xml")
def feed(request: Request, user: str | None = None, limit: int = 200):
    count, latest = _stats()
    media_count, latest_avatar = _media_accounts_signal()
    etag_input = f"{user}|{limit}|{count}|{latest}|{media_count}|{latest_avatar}"
    etag = f'"{sha1(etag_input.encode()).hexdigest()}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    fg = FeedGenerator()
    title = f"Instagram — {user}" if user else "Instagram — Following"
    fg.id(f"{PUBLIC_URL}/instagram.xml" + (f"?user={user}" if user else ""))
    fg.title(title)
    fg.link(href=fg.id(), rel="self")
    fg.updated(datetime.now(UTC))

    entries = rows(user, limit)
    extra_slides = _media_rows([r["id"] for r in entries])
    avatars = _avatar_files()

    for r in entries:
        fe = fg.add_entry(order="append")
        fe.id(f"{PUBLIC_URL}/post/{r['id']}")
        caption = r["caption"] or ""
        first_line = caption.split("\n", 1)[0][:90] or r["kind"] or "post"
        fe.title(f"{r['username']}: {first_line}")
        keys = r.keys()  # sqlite3.Row has no __contains__
        url = r["url"] if "url" in keys and r["url"] else f"https://www.instagram.com/{r['username']}/"
        fe.link(href=url)
        fe.author(name=r["username"])
        fe.updated(_dt(r["scraped_at"]) or datetime.now(UTC))
        if "posted_at" in keys and _dt(r["posted_at"]):
            fe.published(_dt(r["posted_at"]))
        html = ""
        avatar = avatars.get(r["username"])
        if avatar:
            html += f'<p><img src="{PUBLIC_URL}/media/{avatar}" alt="" width="48" height="48" /></p>'
        for slide in ([r["media_file"]] if r["media_file"] else []) + extra_slides.get(r["id"], []):
            html += f'<p><img src="{PUBLIC_URL}/media/{slide}" alt="" /></p>'
        html += f"<p>{escape(caption).replace(chr(10), '<br/>')}</p>"
        # Both dates are also on the entry itself (<published>/<updated>) for readers that sort by
        # those, but spelling them out here means sorting-by-eye works in any reader.
        posted_abs = _dt(r["posted_at"]) if "posted_at" in keys else None
        if r["posted_date"]:
            posted_line = f"Posted {escape(r['posted_date'])}"
            if posted_abs:
                posted_line += f" ({posted_abs.strftime('%Y-%m-%d %H:%M UTC')})"
            meta = [posted_line]
        elif posted_abs:
            meta = [f"Posted {posted_abs.strftime('%Y-%m-%d %H:%M UTC')}"]
        else:
            meta = []
        if "place" in keys and r["place"]:
            meta.append(f"at {escape(r['place'])}")
        scraped_abs = _dt(r["scraped_at"])
        if scraped_abs:
            meta.append(f"saved {scraped_abs.strftime('%Y-%m-%d %H:%M UTC')}")
        meta.append(f'<a href="{escape(url)}">open on Instagram</a>')
        html += f"<p><small>{' · '.join(meta)}</small></p>"
        fe.content(html, type="html")

    return Response(fg.atom_str(pretty=True), media_type="application/atom+xml", headers={"ETag": etag})


@app.get("/users")
def users():
    if not Path(DB_PATH).exists():
        return []
    with closing(_connect()) as con:
        try:
            return [r[0] for r in con.execute("SELECT DISTINCT username FROM posts ORDER BY username")]
        except sqlite3.OperationalError:
            return []


@app.get("/health")
def health():
    count, _ = _stats()
    return {"ok": True, "posts": count}


def _user_counts():
    if not Path(DB_PATH).exists():
        return []
    with closing(_connect()) as con:
        con.row_factory = sqlite3.Row
        try:
            return con.execute(
                "SELECT username, COUNT(*) AS n, MAX(COALESCE(posted_at, scraped_at)) AS latest"
                " FROM posts GROUP BY username ORDER BY username"
            ).fetchall()
        except sqlite3.OperationalError:
            return []


def _recent_runs(limit: int = 10):
    if not Path(DB_PATH).exists():
        return []
    with closing(_connect()) as con:
        con.row_factory = sqlite3.Row
        try:
            return con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        except sqlite3.OperationalError:
            return []  # e.g. the driver hasn't run db_init() yet and the table doesn't exist


def _latest_device():
    """Device props from the most recent run that got far enough to read them (a run that failed
    before connect_device() leaves these NULL)."""
    if not Path(DB_PATH).exists():
        return None
    with closing(_connect()) as con:
        con.row_factory = sqlite3.Row
        try:
            return con.execute(
                "SELECT android_release, android_sdk, device_product FROM runs"
                " WHERE android_release IS NOT NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return None


def _short_error(error: str, limit: int = 140) -> str:
    """First line only, capped: some exceptions (e.g. a uiautomator2 server crash) embed a whole
    multi-KB Java stack trace in their own str(), which would otherwise blow up this table."""
    first_line = error.split("\n", 1)[0]
    return first_line[: limit - 1] + "…" if len(first_line) > limit else first_line


def _duration(run) -> str:
    start, end = _dt(run["started_at"]), _dt(run["finished_at"])
    if not start or not end:
        return "—"
    m, s = divmod(int((end - start).total_seconds()), 60)
    return f"{m}m {s}s" if m else f"{s}s"


def _link_failures(run) -> str:
    """ "sheet/clipboard" failure counts for a run, or "—" against a runs row from before these
    columns existed (SELECT * omits columns the table doesn't have, rather than nulling them)."""
    keys = run.keys()
    if "link_sheet_failures" not in keys and "link_clipboard_failures" not in keys:
        return "—"
    sheet = run["link_sheet_failures"] if "link_sheet_failures" in keys else None
    clip = run["link_clipboard_failures"] if "link_clipboard_failures" in keys else None
    return f"{sheet or 0} sheet / {clip or 0} clipboard"


@app.get("/status", response_class=HTMLResponse)
def status_page():
    total, _ = _stats()
    users = _user_counts()
    runs = _recent_runs()
    device = _latest_device()
    latest = runs[0] if runs else None

    # Next run is expected finished_at + somewhere in [POLL_MIN_HOURS, POLL_MAX_HOURS]; flag it
    # overdue only once we're well past the top of that range, so a run that's simply still in
    # progress (login retries, a slow feed) isn't reported as stuck.
    poll_max_h = float(os.environ.get("POLL_MAX_HOURS", "4.5"))
    overdue = False
    if latest:
        finished = _dt(latest["finished_at"])
        if finished:
            overdue = (datetime.now(UTC) - finished) > timedelta(hours=poll_max_h + 0.5)

    device_line = "no successful run yet"
    if device:
        device_line = f"Android {escape(device['android_release'] or '?')}"
        if device["android_sdk"]:
            device_line += f" (API {escape(device['android_sdk'])})"
        if device["device_product"]:
            device_line += f" — {escape(device['device_product'])}"

    if not latest:
        latest_html = "<p>No scrape runs recorded yet.</p>"
    else:
        status_word = "ERROR" if latest["error"] else ("OVERDUE" if overdue else "OK")
        status_class = "bad" if (latest["error"] or overdue) else "good"
        latest_html = f"""
        <p><span class="badge {status_class}">{status_word}</span>
           finished {escape(latest["finished_at"])} ({_duration(latest)}),
           {latest["new_posts"] if latest["new_posts"] is not None else 0} new post(s)</p>
        {f'<p class="err">{escape(_short_error(latest["error"]))}</p>' if latest["error"] else ""}
        """

    runs_rows = "".join(
        f"<tr><td>{escape(r['started_at'])}</td><td>{_duration(r)}</td>"
        f"<td>{r['new_posts'] if r['new_posts'] is not None else '—'}</td>"
        f"<td>{escape(_link_failures(r))}</td>"
        f'<td class="{"err" if r["error"] else ""}">'
        f"{escape(_short_error(r['error']) if r['error'] else 'ok')}</td></tr>"
        for r in runs
    )
    users_rows = "".join(
        f"<tr><td>{escape(u['username'])}</td><td>{u['n']}</td><td>{escape(u['latest'] or '—')}</td></tr>"
        for u in users
    )

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta http-equiv="refresh" content="60">
<title>Instadroid status</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem;
        background: #fafafa; color: #222; }}
h1 {{ font-size: 1.3rem; }} h2 {{ font-size: 1rem; margin-top: 2rem; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ text-align: left; padding: 0.25rem 0.6rem; border-bottom: 1px solid #ddd; font-size: 0.9rem; }}
.badge {{ display: inline-block; padding: 0.1rem 0.5rem; border-radius: 0.3rem; font-weight: 600; }}
.badge.good {{ background: #d7f5da; color: #146c2e; }}
.badge.bad {{ background: #f8d7da; color: #842029; }}
.err {{ color: #842029; }}
</style></head>
<body>
<h1>Instadroid status</h1>
<p>{device_line}</p>
<h2>Last scrape</h2>
{latest_html}
<h2>Recent runs</h2>
<table><tr><th>Started</th><th>Duration</th><th>New</th><th>Link fails</th><th>Result</th></tr>{runs_rows or '<tr><td colspan="5">none</td></tr>'}</table>
<h2>Totals</h2>
<p>{total} post(s) stored across {len(users)} account(s)</p>
<table><tr><th>Account</th><th>Posts</th><th>Latest</th></tr>{users_rows or '<tr><td colspan="3">none</td></tr>'}</table>
</body></html>"""
    return HTMLResponse(html)
