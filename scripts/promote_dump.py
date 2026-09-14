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
from pathlib import Path

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


def expected(xml: str) -> dict:
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
    for n in root.iter("node"):  # "Liked by <someone> and others" names accounts no parser returns
        for attr in ("text", "content-desc"):
            for handle in re.findall(r"\b(?:Liked by|by|@)\s?([\w.]{3,30})\b", n.get(attr) or ""):
                if handle not in ("others", "you") and not handle[0].isupper():
                    alias(handle, "user")
    ordered = sorted(names, key=len, reverse=True)  # "ab_c" before "ab"
    pattern = re.compile("|".join(rf"(?<![\w.]){re.escape(v)}(?![\w])" for v in ordered)) if ordered else None
    for n in root.iter("node"):
        for attr in ("text", "content-desc"):
            if pattern and (value := n.get(attr)):
                n.set(attr, pattern.sub(lambda m: names[m.group(0)], value))
    return etree.tostring(root, encoding="unicode", xml_declaration=False)


def leftover_text(xml: str) -> list[str]:
    root = etree.fromstring(xml.encode())
    values = {n.get(a) or "" for n in root.iter("node") for a in ("text", "content-desc")}
    return sorted(v for v in values if v and not _CHROME.match(v))


def shape(found: parsing.ScreenParse) -> list:
    """What must survive pseudonymizing: card count and structure, not the text."""
    return [
        [
            (p["kind"], p["complete"], p["headless"], bool(p["caption"]), bool(p["bounds"]))
            for p in found["posts"]
        ],
        [i["seen"] for i in found["story_tray"]],
        len(found["following_list"]),
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true", help="re-record expectations for existing fixtures")
    ap.add_argument("args", nargs="+", help="DUMP PROFILE NAME, or with --update just PROFILE")
    opts = ap.parse_args()
    if opts.update:
        (profile_name,) = opts.args
        versioning.PROFILE = igprofiles.load(profile_name)
        fixtures = igprofiles.fixture(profile_name, "")
        for xml_path in sorted(fixtures.glob("*.xml")):
            out = xml_path.with_suffix(".expected.json")
            out.write_text(json.dumps(expected(xml_path.read_text()), indent=1, ensure_ascii=False) + "\n")
            print("wrote", out.relative_to(ROOT))
        return
    dump, profile_name, name = opts.args
    versioning.PROFILE = igprofiles.load(profile_name)
    raw = Path(dump).read_text()
    clean = pseudonymize(raw)
    if shape(parsing.parse_screen(clean)) != shape(parsing.parse_screen(raw)):
        sys.exit("pseudonymizing changed what the parsers find; not writing a fixture")
    xml_path = igprofiles.fixture(profile_name, f"{name}.xml")
    xml_path.parent.mkdir(exist_ok=True)
    xml_path.write_text("<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n" + clean + "\n")
    result = expected(clean)
    xml_path.with_suffix(".expected.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {xml_path.relative_to(ROOT)} (+ .expected.json):")
    print(
        f"  {len(result['posts'])} post(s), {sum(bool(p['caption']) for p in result['posts'])} with captions;"
        f" {len(result['story_tray'])} story tray item(s); {len(result['following_list'])} following row(s)"
    )
    print("\nReview before committing — text still in the fixture:")
    for value in leftover_text(clean):
        print("  ", value)


if __name__ == "__main__":
    main()
