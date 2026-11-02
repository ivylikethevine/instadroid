"""Captions as HTML and profile links, for the feeds, /browse and the static export."""

import re
from html import escape
from urllib.parse import quote


def profile_url(username: str) -> str:
    return f"https://www.instagram.com/{quote(username)}/"


# Instagram usernames: letters, digits, "_" and ".", at most 30, never ending in "." (so a mention
# at the end of a sentence doesn't swallow the full stop). Hashtags: letters, digits and "_", not all
# digits. Neither may follow a word character, "/" or "@"/"#", which leaves emails and URL
# fragments ("a@b.com", "example.com/#top") alone.
_CAPTION_LINK: re.Pattern[str] = re.compile(
    r"(?<![\w/@#.])@(?P<user>[A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?)(?![\w@])"
    r"|(?<![\w/@#&])#(?P<tag>\w*[^\W\d]\w*)"
)


def caption_html(caption: str) -> str:
    """A caption as HTML: escaped, line breaks kept, and each @mention and #hashtag linked to its
    Instagram page. Matching runs on the raw text, so an escaped entity like &#x27; is never
    mistaken for a hashtag."""
    out: list[str] = []
    pos: int = 0
    user: str | None
    href: str
    m: re.Match[str]
    for m in _CAPTION_LINK.finditer(caption):
        out.append(escape(caption[pos : m.start()]))
        if user := m["user"]:
            href = profile_url(user)
        else:
            href = f"https://www.instagram.com/explore/tags/{quote(m['tag'])}/"
        out.append(f'<a href="{escape(href)}">{escape(m[0])}</a>')
        pos = m.end()
    out.append(escape(caption[pos:]))
    return "".join(out).replace("\n", "<br/>")
