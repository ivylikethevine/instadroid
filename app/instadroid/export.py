"""`scraper.py export`: an archive directory that outlives RETAIN_DAYS.

    data/<account>/<post>.json     one file per post, the archive itself
    media/...                      each post's cover and slides, under their MEDIA_DIR names
    index.html, accounts/<account>/...   static pages built from data/ (HTML format only)

A rerun into the same directory updates the posts still stored and leaves the others in place, so
exporting more often than RETAIN_DAYS keeps everything. Posts are read in batches and pages built one
account at a time, so memory follows the largest account's post count, not the history's size."""

import json
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from html import escape
from itertools import batched
from pathlib import Path
from typing import TypedDict
from urllib.parse import quote, unquote

from shared import jsonvalues, sqlrows
from shared.captions import caption_html, profile_url
from shared.pagestyle import PAGE_STYLE

from . import config, db
from .common import log

BATCH_POSTS: int = 200
PAGE_POSTS: int = 100

_STYLE: str = PAGE_STYLE + "img { max-width: 100%; height: auto; }\n"

type _SortKey = tuple[str, str, Path]  # (sort time, post id, its file)


class ExportedPost(TypedDict):
    """One data/<account>/<post>.json."""

    id: str
    username: str
    kind: str | None
    posted_date: str | None
    posted_at: str | None
    scraped_at: str
    caption: str | None
    place: str | None
    url: str | None
    media: list[str]  # paths under media/, the cover first


class Summary(TypedDict):
    """What one export wrote."""

    posts: int
    accounts: int


class _Account(TypedDict):
    """One row of the HTML index."""

    username: str
    href: str
    posts: int
    latest: str


def safe_name(name: str) -> str:
    """`name` as one path segment: percent-encoded, so it holds no separator, and never "", "." or ".."."""
    encoded: str = quote(name, safe="")
    return "%2E" + encoded[1:] if encoded.startswith(".") else encoded or "_"


def _inside(root: Path, relative: str | Path) -> Path | None:
    """root/relative, unless it resolves (symlinks followed) somewhere outside root."""
    path: Path = root / relative
    return path if path.resolve().is_relative_to(root.resolve()) else None


def _target(root: Path, relative: str | Path) -> Path:
    """_inside(), for a path the export has to write: refuses rather than skips."""
    path: Path | None = _inside(root, relative)
    if path is None:
        raise PermissionError(f"{root / relative} is outside the export directory")
    return path


def _write_whole(root: Path, relative: str, fill: Callable[[Path], object]) -> None:
    """Have `fill` write root/relative through a temporary file beside it, so an interrupted export
    never leaves half of one."""
    path: Path = _target(root, relative)
    tmp: Path = _target(root, relative + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    fill(tmp)
    tmp.replace(path)


def _write(root: Path, relative: Path, content: str) -> None:
    _write_whole(root, str(relative), lambda tmp: tmp.write_text(content, encoding="utf-8"))


def _copy_media(root: Path, file: str) -> bool:
    """Copy one media file into the archive unless it's there already. False when it can't be had: its
    source is gone, or its stored name points outside MEDIA_DIR or the archive's media/."""
    media: Path = _target(root, "media")
    source: Path | None = _inside(config.MEDIA_DIR, file)
    dest: Path | None = _inside(media, file)
    if source is None or dest is None:
        log(f"WARN: export skipped media file with an unsafe name: {file!r}")
        return False
    if dest.exists():
        return True
    if not source.is_file():
        return False
    _write_whole(media, file, lambda tmp: shutil.copyfile(source, tmp))
    return True


def _posts_of(con: sqlite3.Connection, username: str) -> Iterator[sqlite3.Row]:
    """One account's posts, read a batch at a time; no read stays open between batches, so a running
    scrape can still write."""
    after: str = ""
    rows: list[sqlite3.Row]
    while rows := sqlrows.fetch_all(
        con.execute(
            "SELECT * FROM posts WHERE username = ? AND id > ? ORDER BY id LIMIT ?",
            (username, after, BATCH_POSTS),
        )
    ):
        yield from rows
        after = sqlrows.must_str(rows[-1], "id")


def _exported(con: sqlite3.Connection, root: Path, row: sqlite3.Row) -> ExportedPost:
    """A posts row as its JSON form, with its media copied into the archive."""
    post: db.StoredPost = db.stored_post(row)
    slides: list[sqlite3.Row] = sqlrows.fetch_all(
        con.execute("SELECT file FROM media WHERE post_id = ? ORDER BY idx", (post["id"],))
    )
    files: list[str] = [post["media_file"]] if post["media_file"] else []
    files += [sqlrows.must_str(s, 0) for s in slides]
    return {
        "id": post["id"],
        "username": post["username"],
        "kind": post["kind"],
        "posted_date": post["posted_date"],
        "posted_at": post["posted_at"],
        "scraped_at": post["scraped_at"],
        "caption": post["caption"],
        "place": post["place"],
        "url": post["url"],
        "media": [f for f in files if _copy_media(root, f)],
    }


def _read_post(path: Path) -> ExportedPost | None:
    """A post file, or None (with a warning) when it isn't one: a page is never lost to one bad file."""
    data: dict[str, jsonvalues.JSON] = {}
    parsed: jsonvalues.JSON
    try:
        parsed = jsonvalues.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        data = parsed

    def field(name: str) -> str | None:
        value: jsonvalues.JSON = data.get(name)
        return value if isinstance(value, str) else None

    post_id: str | None = field("id")
    username: str | None = field("username")
    scraped_at: str | None = field("scraped_at")
    if post_id is None or username is None or scraped_at is None:
        log(f"WARN: export skipped a post file that isn't valid: {path}")
        return None
    media: jsonvalues.JSON = data.get("media")
    return {
        "id": post_id,
        "username": username,
        "kind": field("kind"),
        "posted_date": field("posted_date"),
        "posted_at": field("posted_at"),
        "scraped_at": scraped_at,
        "caption": field("caption"),
        "place": field("place"),
        "url": field("url"),
        "media": [m for m in media if isinstance(m, str)] if isinstance(media, list) else [],
    }


def _post_html(post: ExportedPost) -> str:
    """One post on an account page, two levels below the archive's root. Links to Instagram only
    over https, so a hand-edited or legacy url can't be a script link in a page opened from disk."""
    images: str = "".join(
        f'<p><img src="../../media/{escape(quote(f))}" alt="" loading="lazy"></p>' for f in post["media"]
    )
    meta: list[str] = []
    posted: str | None
    place: str | None
    url: str | None = post["url"]
    if posted := post["posted_date"] or post["posted_at"]:
        meta.append(f"Posted {escape(posted)}")
    if place := post["place"]:
        meta.append(f"at {escape(place)}")
    meta.append(f"saved {escape(post['scraped_at'][:10])}")
    if url and url.startswith("https://"):
        meta.append(f'<a href="{escape(url)}">open on Instagram</a>')
    return (
        f"<article>{images}<p>{caption_html(post['caption'] or '')}</p>"
        f"<p><small>{' · '.join(meta)}</small></p></article>"
    )


def _html(title: str, body: str) -> str:
    return (
        '<!doctype html>\n<html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width">'
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head>\n"
        f"<body><h1>{escape(title)}</h1>\n{body}\n</body></html>\n"
    )


def _page_name(number: int) -> str:
    return "index.html" if number == 1 else f"page-{number}.html"


def _account_pages(root: Path, account_dir: Path) -> _Account | None:
    """Write one account's pages from its post files, newest first. Only each post's sort key stays in
    memory; a page's posts are read again as it's written. None when no file is readable."""
    username: str = unquote(account_dir.name)
    keys: list[_SortKey] = []
    path: Path
    post: ExportedPost | None
    for path in sorted(account_dir.glob("*.json")):
        if post := _read_post(path):
            username = post["username"]
            keys.append((post["posted_at"] or post["scraped_at"], post["id"], path))
    if not keys:
        return None
    keys.sort(reverse=True)
    pages: list[tuple[_SortKey, ...]] = list(batched(keys, PAGE_POSTS, strict=False))
    number: int
    chunk: tuple[_SortKey, ...]
    shown: ExportedPost | None
    for number, chunk in enumerate(pages, start=1):
        links: list[str] = ['<a href="../../index.html">All accounts</a>']
        links.append(f'<a href="{escape(profile_url(username))}">on Instagram</a>')
        if number > 1:
            links.append(f'<a href="{_page_name(number - 1)}">Newer</a>')
        if number < len(pages):
            links.append(f'<a href="{_page_name(number + 1)}">Older</a>')
        nav: str = f"<p>{' · '.join(links)}</p>"
        articles: str = "".join(_post_html(shown) for key in chunk if (shown := _read_post(key[2])))
        _write(
            root,
            Path("accounts", account_dir.name, _page_name(number)),
            _html(username, nav + articles + nav),
        )
    return {
        "username": username,
        "href": f"accounts/{quote(account_dir.name)}/index.html",
        "posts": len(keys),
        "latest": keys[0][0],
    }


def _write_site(root: Path) -> None:
    """index.html and every account's pages, from everything under data/."""
    data: Path = root / "data"
    account: _Account | None
    accounts: list[_Account] = [
        account for d in sorted(data.iterdir()) if d.is_dir() and (account := _account_pages(root, d))
    ]
    rows: str = "".join(
        f'<tr><td><a href="{escape(a["href"])}">{escape(a["username"])}</a></td>'
        f"<td>{a['posts']}</td><td>{escape(a['latest'][:10])}</td></tr>"
        for a in accounts
    )
    table: str = f"<table><tr><th>Account</th><th>Posts</th><th>Latest</th></tr>{rows}</table>"
    _write(root, Path("index.html"), _html("Instagram archive", table))


def export_posts(con: sqlite3.Connection, directory: str, html: bool = True) -> Summary:
    """Export every stored post under `directory`, created if needed; nothing is written outside it,
    whatever a stored name or a symlink already in it says. `html` also builds the static pages."""
    root: Path = Path(directory).resolve()
    (root / "data").mkdir(parents=True, exist_ok=True)
    usernames: list[str] = [
        sqlrows.must_str(r, 0)
        for r in sqlrows.fetch_all(
            con.execute("SELECT DISTINCT username FROM posts WHERE username IS NOT NULL ORDER BY username")
        )
    ]
    posts: int = 0
    username: str
    row: sqlite3.Row
    for username in usernames:
        for row in _posts_of(con, username):
            post: ExportedPost = _exported(con, root, row)
            relative: Path = Path("data", safe_name(username), safe_name(post["id"]) + ".json")
            _write(root, relative, json.dumps(post, ensure_ascii=False, indent=1) + "\n")
            posts += 1
    if html:
        _write_site(root)
    return {"posts": posts, "accounts": len(usernames)}
