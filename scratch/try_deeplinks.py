"""Open Instagram deep links on the device and report which screen each lands on.

For each URI: force-stop Instagram, `am start -a VIEW -d <uri>`, wait, dump the hierarchy to
DEBUG_DIR/deeplinks/, and print the action bar's title and which of the feed markers are on screen.
Looks only; taps nothing. Device-driving, so not while a scrape runs. Run it in the app image:

    docker compose run --rm --no-deps -v "$PWD/app:/app:ro" -v "$PWD/scratch:/scratch:ro" \
        app python /scratch/try_deeplinks.py [URI...]

With no URI it tries the candidates found in the 450 manifest and dex strings for reaching the
Following feed without the switcher.
"""

import re
import sys
import time

sys.path.insert(0, "/app")

from instadroid import config, device  # noqa: E402
from lxml import etree  # noqa: E402

CANDIDATES = [
    "instagram://mainfeed",
    "instagram://peoplefeed",
    "instagram://mainfeed?feed_type=following",
    "instagram://mainfeed?variant=following",
    "https://www.instagram.com/?variant=following",
]
MARKERS = (
    "action_bar_title",
    "feed_tab",
    "reels_tray_container",
    "row_feed_profile_header",
    "unified_follow_list_view_pager",
)

d = device.connect_device()
out = config.DEBUG_DIR / "deeplinks"
out.mkdir(parents=True, exist_ok=True)
for uri in sys.argv[1:] or CANDIDATES:
    d.shell(["am", "force-stop", config.IG_PKG])
    time.sleep(2)
    started = d.shell(["am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", uri, config.IG_PKG]).output
    time.sleep(10)
    xml = d.dump_hierarchy()
    (out / (re.sub(r"[^a-z_=]+", "_", uri) + ".xml")).write_text(xml)
    nodes = list(etree.fromstring(xml.encode()).iter("node"))
    ids = {(n.get("resource-id") or "").rsplit("/", 1)[-1] for n in nodes}
    titles = [n.get("text") for n in nodes if (n.get("resource-id") or "").endswith("/action_bar_title")]
    status = "resolved" if "Status: ok" in started else "NOT RESOLVED"
    print(f"{uri}\n  {status}; in front: {d.app_current().get('package')}; title: {titles}")
    print(f"  markers: {[m for m in MARKERS if m in ids]}")
d.shell(["am", "force-stop", config.IG_PKG])
