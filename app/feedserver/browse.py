"""/browse: the stored history for a person to read, as an account list, each account's timeline and
a caption search, one page at a time."""

import sqlite3
from datetime import datetime
from html import escape
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse
from shared.pagestyle import PAGE_STYLE
from shared.timestamps import parse_iso

from . import queries, render
from .entries import Entry, post_entries
from .queries import string, text

router: APIRouter = APIRouter()

DEFAULT_LIMIT: int = 50

_STYLE: str = PAGE_STYLE + "input { font: inherit; padding: 0.2rem 0.4rem; }\n"


def _link(**params: str | None) -> str:
    """A /browse URL with the given parameters, escaped for an attribute. Relative, and without the token:
    a browser that got here through the basic-auth prompt sends its credentials along."""
    query: dict[str, str] = {k: v for k, v in params.items() if v}
    return escape("/browse" + (f"?{urlencode(query)}" if query else ""))


def _search_form(user: str | None, q: str | None) -> str:
    scope: str = f'<input type="hidden" name="user" value="{escape(user)}">' if user else ""
    return (
        f'<form action="/browse" method="get">{scope}'
        f'<input type="search" name="q" value="{escape(q or "")}" placeholder="Search captions"> '
        "<button>Search</button></form>"
    )


def _accounts_table(accounts: list[sqlite3.Row]) -> str:
    """Each account with its post count and latest post, linked to its timeline."""
    rows: list[str] = []
    u: sqlite3.Row
    for u in accounts:
        username: str = string(u, "username")
        latest: datetime | None = parse_iso(text(u, "latest"))
        rows.append(
            f'<tr><td><a href="{_link(user=username)}">{escape(username)}</a></td>'
            f"<td>{string(u, 'n')}</td><td>{render.local(latest) if latest else '—'}</td></tr>"
        )
    return (
        "<table><tr><th>Account</th><th>Posts</th><th>Latest</th></tr>"
        + ("".join(rows) or '<tr><td colspan="3">none</td></tr>')
        + "</table>"
    )


def _article(entry: Entry) -> str:
    author: str = entry.author or ""
    return f'<article><h2><a href="{_link(user=author)}">{escape(author)}</a></h2>{entry.html}</article>'


def _page(heading: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{escape(heading)}</title><style>{_STYLE}</style></head>
<body><p><a href="/browse">All accounts</a> · <a href="/status">Status</a></p>
<h1>{escape(heading)}</h1>
{body}
</body></html>"""
    )


@router.get("/browse", response_class=HTMLResponse, summary="Browse and search stored posts")
def browse(
    user: Annotated[str | None, Query(description="One account's timeline.")] = None,
    q: Annotated[
        str | None,
        Query(description="Caption search: every word must match, each as a prefix of a caption word."),
    ] = None,
    before: Annotated[str | None, Query(description="The paging cursor's time, from the Older link.")] = None,
    before_id: Annotated[
        str | None, Query(description="The paging cursor's post, from the Older link.")
    ] = None,
    limit: int = DEFAULT_LIMIT,
) -> HTMLResponse:
    """A plain-HTML page: the account list, or with `user` or `q`, matching posts newest first, `limit`
    (capped at 500) to a page."""
    con: sqlite3.Connection
    if not user and not (q or "").strip():
        with queries.connection() as con:
            return _page("Browse", _search_form(None, None) + _accounts_table(queries.user_counts(con)))
    size: int = queries.limit(limit)
    cursor: tuple[str, str] | None = (before, before_id) if before and before_id else None
    with queries.connection() as con:
        rows: list[sqlite3.Row] = queries.archive_page(con, user, q, cursor, size)
        shown: list[sqlite3.Row] = rows[:size]
        entries: list[Entry] = post_entries(con, shown)
    older: str = ""
    if len(rows) > size:
        href: str = _link(
            user=user,
            q=q,
            before=string(shown[-1], "sort_at"),
            before_id=string(shown[-1], "id"),
            limit=None if size == DEFAULT_LIMIT else str(size),
        )
        older = f'<p><a href="{href}" rel="next">Older</a></p>'
    heading: str = (f'Search: "{q}"' if q else "Posts") + (f" in {user}" if user else "")
    body: str = "".join(_article(e) for e in entries) or "<p>No posts.</p>"
    return _page(heading, _search_form(user, q) + body + older)
