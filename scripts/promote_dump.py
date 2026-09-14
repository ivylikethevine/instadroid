"""Turn a real hierarchy dump into a replay fixture for a version profile.

    python scripts/promote_dump.py local/data/debug/last_hierarchy.xml v446 home_feed
    python scripts/promote_dump.py --update v445        # re-record every v445 fixture's expectations

Writes app/igprofiles/<profile>/fixtures/<name>.xml, with the accounts, display names, places and
captions it can identify replaced by placeholders, and <name>.expected.json, what the parsers find
in it (tests/test_replay.py checks that stays true). Then it prints every text value still left in
the dump: pseudonymizing only catches what the parsers recognise, so read that list before
committing and fix anything personal by hand (then run --update).

Parsing an unfamiliar version's dump under the previous profile is also the quickest drift check:
a feed screen that yields no posts, or posts without captions, means selectors need updating.
Needs the app's requirements (run it from the dev venv); never touches a device.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import igprofiles  # noqa: E402
from instadroid import parsing, versioning  # noqa: E402
from lxml import etree  # noqa: E402

# UI chrome that is the same for everyone; left out of the review list.
_CHROME = re.compile(
    r"^(Back|Home|Overview|Send post\..*|Like|Comment|Save|More|Following|For you|\d+(:\d+)?( [AP]M)?|"
    r"\d+ (second|minute|hour|day|week)s? ago|[A-Z][a-z]+ \d{1,2}(, \d{4})?|Yesterday|Battery.*|Ethernet\.)$"
)


def expected(xml: str) -> dict[str, Any]:
    return json.loads(json.dumps(parsing.parse_screen(xml)))  # plain JSON types, as stored


def pseudonymize(xml: str) -> str:
    """Replace every account, display name, place and caption the parsers find with a placeholder."""
    found = parsing.parse_screen(xml)
    names: dict[str, str] = {}

    def alias(value: str, kind: str) -> None:
        if value and value not in names:
            names[value] = f"{kind}{sum(v.startswith(kind) for v in names.values()) + 1}"

    for p in found["posts"]:
        alias(p["username"], "user")
        alias(p["place"], "Place ")
        if m := re.search(r"\bby ([^,]+),", p["alt"]):
            alias(m.group(1), "Display ")
        alias(p["caption"].rstrip("…").strip(), "Caption ")
    for item in found["story_tray"]:
        alias(item["username"], "user")
    for username in found["following_list"]:
        alias(username, "user")
    root = etree.fromstring(xml.encode())
    selectors = versioning.PROFILE.selectors
    for n in root.iter("node"):  # names on screen that no parser returns
        for attr in ("text", "content-desc"):
            value = n.get(attr) or ""
            # A card header the post parser skipped (a suggested post, a card cut off at the edge).
            if m := selectors["header_desc"].match(value):
                alias(m["user"], "user")
                alias(m["place"] or "", "Place ")
            # Every story tray item, including the logged-in account's own (index 0, never parsed).
            if m := selectors["story_item_desc"].match(value):
                alias(m["user"], "user")
            # "Liked by <someone>", "by <someone>", an @mention, "<someone> and 3 others",
            # "Profile picture of <someone>", "<someone>'s story".
            handles = re.findall(r"(?:\b(?:Liked by|by|of)\s|@)([\w.]{3,30})\b", value)
            handles += re.findall(r"^([\w.]{3,30})(?: and \d+ others?$|'s story\b)", value)
            # A collab post's two authors; both lowercase-initial, so "Search and explore" stays.
            if (m := re.match(r"^([\w.]{3,30}) and ([\w.]{3,30})$", value)) and not (
                m.group(1)[0].isupper() or m.group(2)[0].isupper()
            ):
                handles += [m.group(1), m.group(2)]
            for handle in handles:
                if handle not in ("others", "you") and not handle[0].isupper():
                    alias(handle, "user")
            # A Reel's audio credit, "<artist> · <track>" (or "<account> · Original audio").
            if m := re.match(r"^\s*(.+?) · (.+?)\s*$", value):
                alias(m.group(1), "Artist ")
                if m.group(2) != "Original audio":
                    alias(m.group(2), "Track ")
            # "Follow <display name>" on a suggested account.
            if (m := re.match(r"^Follow (.+)$", value)) and m.group(1) not in ("back", "Back"):
                alias(m.group(1), "Display ")
    ordered = sorted(names, key=len, reverse=True)  # "ab_c" before "ab"
    pattern = re.compile("|".join(rf"(?<![\w.]){re.escape(v)}(?![\w])" for v in ordered)) if ordered else None
    for n in root.iter("node"):
        for attr in ("text", "content-desc", "hint"):
            if pattern and (value := n.get(attr)):
                n.set(attr, pattern.sub(lambda m: names[m.group(0)], value))
    return etree.tostring(root, encoding="unicode", xml_declaration=False)


def leftover_text(xml: str) -> list[str]:
    root = etree.fromstring(xml.encode())
    values = {n.get(a) or "" for n in root.iter("node") for a in ("text", "content-desc", "hint")}
    return sorted(v for v in values if v and not _CHROME.match(v))


def shape(found: parsing.ScreenParse) -> list[Any]:
    """What must survive pseudonymizing: card count and structure, not the text."""
    return [
        [
            (p["kind"], p["complete"], p["headless"], bool(p["caption"]), bool(p["bounds"]))
            for p in found["posts"]
        ],
        [i["seen"] for i in found["story_tray"]],
        len(found["following_list"]),
    ]


@dataclass
class Promoted:
    xml_path: Path
    result: dict[str, Any]
    leftovers: list[str]


def promote(dump: Path, profile_name: str, name: str) -> Promoted:
    """Scrub `dump` into igprofiles/<profile>/fixtures/<name>.xml and record what the parsers find in
    it under that profile. Raises ValueError, writing nothing, if scrubbing changed what parses."""
    versioning.PROFILE = igprofiles.load(profile_name)
    raw = dump.read_text()
    clean = pseudonymize(raw)
    if shape(parsing.parse_screen(clean)) != shape(parsing.parse_screen(raw)):
        raise ValueError(f"pseudonymizing {dump.name} changed what the parsers find; not writing a fixture")
    xml_path = igprofiles.fixture(profile_name, f"{name}.xml")
    xml_path.parent.mkdir(exist_ok=True)
    xml_path.write_text("<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n" + clean + "\n")
    result = expected(clean)
    xml_path.with_suffix(".expected.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    return Promoted(xml_path, result, leftover_text(clean))


def rerecord(profile_name: str) -> list[Path]:
    """Re-record every fixture's .expected.json under `profile_name`'s current selectors."""
    versioning.PROFILE = igprofiles.load(profile_name)
    written = []
    for xml_path in sorted(igprofiles.fixture(profile_name, "").glob("*.xml")):
        out = xml_path.with_suffix(".expected.json")
        out.write_text(json.dumps(expected(xml_path.read_text()), indent=1, ensure_ascii=False) + "\n")
        written.append(out)
    return written


def print_promoted(promoted: Promoted) -> None:
    result = promoted.result
    print(f"wrote {promoted.xml_path.relative_to(ROOT)} (+ .expected.json):")
    print(
        f"  {len(result['posts'])} post(s), {sum(bool(p['caption']) for p in result['posts'])} with captions;"
        f" {len(result['story_tray'])} story tray item(s); {len(result['following_list'])} following row(s)"
    )
    print("\nReview before committing — text still in the fixture:")
    for value in promoted.leftovers:
        print("  ", value)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true", help="re-record expectations for existing fixtures")
    ap.add_argument("args", nargs="+", help="DUMP PROFILE NAME, or with --update just PROFILE")
    opts = ap.parse_args()
    if opts.update:
        (profile_name,) = opts.args
        for out in rerecord(profile_name):
            print("wrote", out.relative_to(ROOT))
        return
    dump, profile_name, name = opts.args
    try:
        print_promoted(promote(Path(dump), profile_name, name))
    except ValueError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
