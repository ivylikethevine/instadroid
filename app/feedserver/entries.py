"""What a feed says about each post, story and alert, built once and rendered as Atom or JSON Feed
(feeds.py)."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape

from shared.captions import caption_html, profile_url
from shared.sqlrows import opt_str
from shared.timestamps import parse_iso

from . import queries, render, settings
from .queries import string, text


@dataclass(frozen=True)
class Entry:
    id: str
    title: str
    url: str
    html: str
    updated: datetime
    published: datetime | None = None
    author: str | None = None
    author_url: str | None = None
    avatar: str | None = None  # a media file
    image: str | None = None  # a media file, the entry's thumbnail


def alert_entries(alerts: list[sqlite3.Row]) -> list[Entry]:
    """Each alert gets a new id per raise, so a reader shows it again if it's resolved and raised later."""
    public_url: str = settings.PUBLIC_URL
    out: list[Entry] = []
    alert: sqlite3.Row
    for alert in alerts:
        message: str = string(alert, "message")
        raised: datetime = parse_iso(text(alert, "raised_at")) or datetime.now(UTC)
        out.append(
            Entry(
                id=f"{public_url}/alert/{string(alert, 'kind')}/{string(alert, 'raised_at')}",
                title=f"⚠ instadroid needs attention: {message[:90]}",
                url=f"{public_url}/status",
                html=(
                    f"<p>{escape(message)}</p><p><small>since {render.local(raised)} · "
                    f'<a href="{public_url}/status">status page</a></small></p>'
                ),
                updated=raised,
                published=raised,
            )
        )
    return out


def post_entries(con: sqlite3.Connection, posts: list[sqlite3.Row]) -> list[Entry]:
    extra_slides: dict[str, list[str]] = queries.extra_slides(con, [string(r, "id") for r in posts])
    avatars: dict[str, str] = queries.avatar_files(con, sorted({string(r, "username") for r in posts}))
    out: list[Entry] = []
    username: str
    kind: str | None
    media_file: str | None
    posted_date: str | None
    place: str | None
    scraped_abs: datetime | None
    r: sqlite3.Row
    for r in posts:
        username, kind, media_file = string(r, "username"), text(r, "kind"), text(r, "media_file")
        caption: str = text(r, "caption") or ""
        first_line: str = caption.split("\n", 1)[0][:90] or kind or "post"
        marker: str = "▶ " if kind == "video" else ""  # Reels and videos are stored as kind "video"
        url: str = opt_str(r, "url") or profile_url(username)
        posted_abs: datetime | None = parse_iso(opt_str(r, "posted_at"))
        html: str = ""
        avatar: str | None = avatars.get(username)
        if avatar:
            html += f'<p><img src="{render.media_url(avatar)}" alt="" width="48" height="48" /></p>'
        slide: str
        for slide in ([media_file] if media_file else []) + extra_slides.get(string(r, "id"), []):
            html += f"<p>{render.img(slide)}</p>"
        html += f"<p>{caption_html(caption)}</p>"
        # Both dates are also on the entry itself (published/updated) for readers that sort by
        # those, but spelling them out here means sorting-by-eye works in any reader.
        meta: list[str] = []
        if posted_date := text(r, "posted_date"):
            meta.append(
                f"Posted {escape(posted_date)}" + (f" ({render.local(posted_abs)})" if posted_abs else "")
            )
        elif posted_abs:
            meta.append(f"Posted {render.local(posted_abs)}")
        if place := opt_str(r, "place"):
            meta.append(f"at {escape(place)}")
        if scraped_abs := parse_iso(text(r, "scraped_at")):
            meta.append(f"saved {render.local(scraped_abs)}")
        meta.append(f'<a href="{escape(url)}">open on Instagram</a>')
        html += f"<p><small>{' · '.join(meta)}</small></p>"
        out.append(
            Entry(
                id=f"{settings.PUBLIC_URL}/post/{string(r, 'id')}",
                title=f"{marker}{username}: {first_line}",
                url=url,
                html=html,
                updated=scraped_abs or datetime.now(UTC),
                published=posted_abs,
                author=username,
                author_url=profile_url(username),
                avatar=avatar,
                image=media_file,
            )
        )
    return out


def story_entries(stories: list[sqlite3.Row]) -> list[Entry]:
    out: list[Entry] = []
    username: str
    media_file: str | None
    r: sqlite3.Row
    for r in stories:
        username, media_file = string(r, "username"), text(r, "media_file")
        scraped: datetime = parse_iso(text(r, "scraped_at")) or datetime.now(UTC)
        html: str = f"<p>{render.img(media_file)}</p>" if media_file else ""
        html += f"<p><small>saved {render.local(scraped)}</small></p>"
        out.append(
            Entry(
                id=f"{settings.PUBLIC_URL}/story/{string(r, 'id')}",
                title=f"{username}'s story",
                url=profile_url(username),
                html=html,
                updated=scraped,
                published=scraped,
                author=username,
                author_url=profile_url(username),
                image=media_file,
            )
        )
    return out
