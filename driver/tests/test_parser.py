from pathlib import Path

import pytest
import scraper

FIXTURE = (Path(__file__).parent / "fixture_feed.xml").read_text()


def test_parse_hierarchy_finds_both_cards_and_skips_sponsored():
    posts = scraper.parse_hierarchy(FIXTURE)
    assert [p["username"] for p in posts] == ["someone_nice", "other_user"]


def test_headless_top_card_is_identified_from_caption_and_media():
    top = scraper.parse_hierarchy(FIXTURE)[0]
    assert top["headless"] is True
    assert top["kind"] == "video"  # "Reel by ..." maps to video
    assert top["caption"] == "Top card caption…"
    assert top["posted_date"] == "3 days ago"
    assert top["complete"] is True
    assert top["share_bounds"] == "[390,900][453,1021]"
    assert top["clip_top"] == 289


def test_full_card_fields():
    card = scraper.parse_hierarchy(FIXTURE)[1]
    assert card["kind"] == "carousel"
    assert card["place"] == "San Diego, California"
    assert card["posted_date"] == "21 hours ago"
    assert card["bounds"] == "[0,1287][1080,2000]"
    assert card["alt"].startswith("Photo 1 of 7")
    assert card["caption"] == "Second caption"
    assert card["complete"] is True


def test_post_id_ignores_counts_dates_and_kind():
    base = {"username": "u", "kind": "photo", "caption": "", "alt": "Photo 1 of 3 by U, 5 likes, 2 comments"}
    later = {**base, "kind": "carousel", "alt": "Photo 2 of 3 by U, 9 likes, 4 comments"}
    assert scraper.post_id(base) == scraper.post_id(later)
    assert scraper.post_id({**base, "caption": "hello"}) != scraper.post_id(base)


@pytest.mark.parametrize(
    ("desc", "expected"),
    [
        (
            "nykky posted a video in Tiger's Pictionary at #1 Fifth Ave August 29",
            ("nykky", "video", "Tiger's Pictionary at #1 Fifth Ave", "August 29"),
        ),
        ("some.one posted a photo 6 days ago", ("some.one", "photo", None, "6 days ago")),
        (
            "club posted a carousel in San Diego, California 3 days ago",
            ("club", "carousel", "San Diego, California", "3 days ago"),
        ),
        ("Sponsored", None),
    ],
)
def test_header_regex(desc, expected):
    m = scraper.SELECTORS["header_desc"].match(desc)
    if expected is None:
        assert m is None
    else:
        assert (m.group("user"), m.group("kind"), m.group("place"), m.group("date")) == expected


def test_clean_caption_strips_user_and_more():
    assert scraper.clean_caption("user Hello there… more", "user") == "Hello there…"
    assert scraper.clean_caption("user Short", "user") == "Short"


def test_permalink_regex_accepts_reel_and_p_with_tracking_params():
    rx = scraper.SELECTORS["permalink"]
    assert rx.match("https://www.instagram.com/reel/DdCSb4WsvUs/?stkn=abc").group("code") == "DdCSb4WsvUs"
    assert rx.match("https://www.instagram.com/p/Dc9fdRTSKE5/").group("type") == "p"
    assert rx.match("https://www.instagram.com/someone/") is None
