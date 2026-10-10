"""Replay a recorded run (a RECORD_DIR tape, see app/instadroid/recording.py) through the scraper, offline.

Runs scrape_once() against tests/replaydevice.py's ReplayDevice with a throwaway database and media
directory and no pauses, then prints how far through the tape it got, every action that diverged from
the recording, and what it stored. A real tape holds real accounts and captions: keep it under local/.

    local/.venv/bin/python scratch/replay_tape.py local/data/debug/<run>/tape/<timestamp> [DATABASE]

DATABASE is copied and used as the run's starting point: without the posts the recorded run already had
stored, the replay tries to capture them and diverges from the tape at the first one.
"""

import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "app"), str(REPO)]

from instadroid import capture, config, db, device, scrape, versioning  # noqa: E402

from tests.replaydevice import ReplayDevice  # noqa: E402

tape = Path(sys.argv[1])
ig_version = "450.0.0.50.77"
work = Path(tempfile.mkdtemp(prefix="replay-tape."))
config.DB_PATH = str(work / "posts.sqlite")
if len(sys.argv) > 2:
    shutil.copy(sys.argv[2], config.DB_PATH)
config.MEDIA_DIR = work / "media"
config.DEBUG_DIR = work / "debug"
config.BACKUP_DIR = str(work / "backups")
config.FOLLOWING_REFRESH_DAYS = 0
config.FRESHRSS_REFRESH_URL = ""
config.CLIPBOARD_TIMEOUT = 0.01
config.PERMALINK_BACKFILL_PER_RUN = 0
capture._clearing = True

d = ReplayDevice(tape, ig_version=ig_version)
device.human_pause = lambda lo=1.0, hi=3.0: d.tick() and None
time.sleep = lambda seconds: None
versioning.activate_profile(ig_version)

error = None
try:
    stats = scrape.scrape_once(d, db.db_init())
except Exception as e:  # the point is to see where a replay stops
    error, stats = e, None

acted = [i for i, step in enumerate(d.tape) if step["after"]]
print(f"tape: {len(d.tape)} steps, {len(acted)} of them after an action")
print(f"replay stopped on step {d.step}; finished: {d.finished}; actions past the end: {len(d.past_end)}")
print(f"divergences: {len(d.divergences)}")
for step, action in d.divergences[:40]:
    print(f"  on step {step}: {action}  (tape next: {next((d.tape[i]['after'] for i in acted if i > step), None)})")
if error:
    print("error:", repr(error))
if stats:
    print("stored:", stats["new"], "new posts;", stats["metrics"].get("new_stories"), "new stories")
print("scratch files in", work)
