"""The file that subscribes a FreshRSS user to this instance's account feeds for good, written without
asking the server, so before the scraper's first run too. Imported once (scripts/freshrss-setup.sh
does it), it creates a category whose Dynamic OPML URL is /opml, so FreshRSS follows the accounts from
then on.

    python -m feedserver.freshrss > instadroid.opml

The URLs start with PUBLIC_URL when it is set, else with the loopback address the server listens on,
which a FreshRSS on this host's network (the compose service) reaches."""

import sys
from xml.etree.ElementTree import Element, SubElement, tostring

from . import render, settings
from .feeds import INSTAGRAM_HOME, category_outline, feed_outline


def category_file(base: str) -> bytes:
    """An OPML file of the one category that follows `base`/opml. FreshRSS skips a category without a
    feed: the stories feed is the one /opml lists whatever is stored."""
    root: Element = Element("opml", version="2.0")
    SubElement(SubElement(root, "head"), "title").text = "Instadroid"
    category: Element = category_outline(base)
    SubElement(root, "body").append(category)
    category.append(
        feed_outline("Instagram — Stories", render.feed_url(base, "/stories.xml"), INSTAGRAM_HOME)
    )
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode").encode() + b"\n"


def main() -> int:
    base: str = settings.PUBLIC_URL if settings.PUBLIC_URL_SET else f"http://127.0.0.1:{settings.FEED_PORT}"
    sys.stdout.buffer.write(category_file(base))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
