"""Recording a run as a tape (recording.Recorder) and playing it back (tests/replaydevice.py)."""

import json
import sqlite3
from pathlib import Path

import pytest
from instadroid import config, db, device, recording, scrape, uidevice
from shared import sqlrows

from tests.deviceflows import feed_device
from tests.fakedevice import IG_PKG, LAUNCHER_PKG, FakeDevice, hierarchy, node
from tests.replaydevice import ReplayDevice, load_tape, same_action

pytestmark: pytest.MarkDecorator = pytest.mark.usefixtures("fast_offline")


def _post_ids(con: sqlite3.Connection) -> list[str]:
    return [
        sqlrows.must_str(r, 0) for r in sqlrows.fetch_all(con.execute("SELECT id FROM posts ORDER BY id"))
    ]


def test_a_recorded_run_replays_to_the_same_posts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_CAROUSEL_SLIDES", 1)
    monkeypatch.setattr(config, "MAX_SCROLLS", 1)
    recorded: sqlite3.Connection = db.db_init()
    first: scrape.RunStats = scrape.scrape_once(
        recording.Recorder(feed_device(), tmp_path / "tape"), recorded
    )

    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "replay.sqlite"))
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "replay-media")
    d: ReplayDevice = ReplayDevice(tmp_path / "tape")

    def pause(lo: float = 1.0, hi: float = 3.0) -> None:
        d.tick()

    monkeypatch.setattr(device, "human_pause", pause)
    replayed: sqlite3.Connection = db.db_init()
    second: scrape.RunStats = scrape.scrape_once(d, replayed)
    assert d.divergences == []
    assert d.finished
    assert second["new"] == first["new"] == 2
    assert _post_ids(replayed) == _post_ids(recorded)


# --- the recorder --------------------------------------------------------------------------------


def _screens() -> dict[str, str]:
    return {
        "a": hierarchy(
            node(text="Open", bounds=(0, 100, 200, 200), goto="b"),
            node(text="Open", bounds=(0, 300, 200, 400)),
        ),
        "b": hierarchy(
            node(text="Field", cls="android.widget.EditText", bounds=(0, 100, 200, 200), clip="copied")
        ),
    }


def test_a_step_is_a_screen_and_the_actions_that_led_to_it(tmp_path: Path) -> None:
    d: FakeDevice = FakeDevice(_screens(), "a", back={"b": "a"})
    rec: recording.Recorder = recording.Recorder(d, tmp_path)
    assert rec(text="Open").count == 2
    assert rec(text="Open")[1].info["bounds"]["top"] == 300
    rec(text="Open")[0].click()
    assert rec(text="Field").exists(timeout=2)
    rec(text="Field").set_text("secret")
    rec.click(100, 150)
    assert rec.clipboard == "copied"
    rec.swipe(500, 1500, 500, 500)
    rec.press("back")
    assert rec.app_current()["package"] == IG_PKG
    rec.app_start(IG_PKG, activity="x")
    rec.dump_hierarchy()

    tape: list[recording.Step] = load_tape(tmp_path)
    assert [step["after"] for step in tape] == [
        [],
        [{"kind": "tap", "selector": {"text": "Open"}, "index": 0}],
        [{"kind": "type", "selector": {"text": "Field"}}, {"kind": "click", "point": [100, 150]}],
        [{"kind": "swipe", "path": [500, 1500, 500, 500]}, {"kind": "press", "key": "back"}],
        [{"kind": "start"}],
    ]
    assert tape[2].get("clipboard") == "copied" and tape[3].get("package") == IG_PKG
    assert (tmp_path / tape[1]["screen"]).read_text() == _screens()["b"]
    assert "secret" not in (tmp_path / recording.TAPE_FILE).read_text()  # typed text is never recorded


def test_the_rest_of_the_device_is_passed_through(tmp_path: Path) -> None:
    d: FakeDevice = FakeDevice(_screens(), "a")
    rec: recording.Recorder = recording.Recorder(d, tmp_path)
    assert rec.clipboard == ""  # read before any step exists
    rec.set_clipboard("x")
    assert d.clipboard == "x"
    assert rec.info == d.info and rec.window_size() == d.window_size() and rec.app_list() == d.app_list()
    assert rec.implicitly_wait(3) == 3 and rec.screenshot().size == d.screenshot().size
    assert rec.shell("getprop ro.product.name").output == "redroid"


def test_a_screen_that_changes_by_itself_is_a_step_with_no_action(tmp_path: Path) -> None:
    now: list[float] = [0.0]
    d: FakeDevice = FakeDevice(_screens(), "a")
    rec: recording.Recorder = recording.Recorder(d, tmp_path, clock=lambda: now[0])
    assert rec(text="Open").exists()
    d.screen = "b"  # a load finishing, an interstitial
    assert not rec(text="Open").exists()  # looked at again too soon for a new dump
    assert len(load_tape(tmp_path)) == 1
    now[0] += recording.STALE_SECONDS + 1
    assert not rec(text="Open").exists()
    now[0] += recording.STALE_SECONDS + 1
    assert not rec(text="Open").exists()  # dumped again, unchanged: no step
    assert [step["after"] for step in load_tape(tmp_path)] == [[], []]


def test_a_tape_that_cannot_be_written_stops_the_recording_not_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rec: recording.Recorder = recording.Recorder(FakeDevice(_screens(), "a"), tmp_path)
    (tmp_path / "0000.xml").mkdir()  # the first step's file name is taken by a directory
    assert rec(text="Open").exists()
    rec(text="Open")[0].click()
    assert rec(text="Field").exists()
    assert "recording stopped" in capsys.readouterr().out
    assert rec.clipboard == ""  # reading it no longer writes anything
    assert not (tmp_path / recording.TAPE_FILE).exists()


def test_connecting_with_a_record_dir_wraps_the_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d: FakeDevice = FakeDevice(_screens(), "a")

    def connect(addr: str, timeout: float = 0) -> str:
        return "connected"

    def booted(addr: str, timeout: float) -> bool:
        return True

    def fake_u2(addr: str) -> FakeDevice:
        return d

    monkeypatch.setattr(device.adbutils.adb, "connect", connect)
    monkeypatch.setattr(device.tune, "wait_for_boot", booted)
    monkeypatch.setattr(device.u2, "connect", fake_u2)
    monkeypatch.setattr(config, "TUNE_ON_CONNECT", False)
    monkeypatch.setattr(config, "RECORD_DIR", str(tmp_path))
    recorded: uidevice.Device = device.connect_device()
    assert isinstance(recorded, recording.Recorder)
    recorded.dump_hierarchy()
    assert len(list(tmp_path.glob("*/tape.json"))) == 1


# --- the replay device ---------------------------------------------------------------------------


def _tape(tmp_path: Path, steps: list[recording.Step]) -> Path:
    screens: dict[str, str] = {"a.xml": _screens()["a"], "b.xml": _screens()["b"], "l.xml": LAUNCHER}
    name: str
    for name in {step["screen"] for step in steps}:
        (tmp_path / name).write_text(screens[name])
    (tmp_path / recording.TAPE_FILE).write_text(json.dumps({"version": 1, "steps": steps}))
    return tmp_path


LAUNCHER: str = '<hierarchy><node package="com.android.launcher3" bounds="[0,0][1080,2340]"/></hierarchy>'


def test_replay_follows_the_tape_and_keeps_what_diverges(tmp_path: Path) -> None:
    d: ReplayDevice = ReplayDevice(
        _tape(
            tmp_path,
            [
                {"screen": "a.xml", "after": []},
                {"screen": "b.xml", "after": [{"kind": "click", "point": [100, 150]}], "clipboard": "link"},
                {
                    "screen": "a.xml",
                    "after": [{"kind": "press", "key": "back"}, {"kind": "swipe", "path": [5, 9, 5, 1]}],
                },
                {"screen": "l.xml", "after": []},
                {"screen": "a.xml", "after": [{"kind": "start"}], "package": IG_PKG},
            ],
        )
    )
    d.press("back")  # the tape has a click next
    d.click(900, 900)  # and not there
    assert [action["kind"] for _, action in d.divergences] == ["press", "click"] and d.step == 0
    d.click(130, 140)
    assert d.step == 1 and d.clipboard == "link" and d(text="Field").exists()
    d.press("back")
    assert d.step == 1  # one of the two actions the next step followed
    assert not d.tick()  # and nothing changes by itself between them
    d.swipe(500, 1500, 500, 400)
    assert d.step == 2 and not d.finished
    assert not d(text="Field").exists()  # no wait, so no step is entered
    assert not d(text="Field").exists(timeout=5)  # waited through the launcher step
    assert d.step == 3 and d.app_current()["package"] == LAUNCHER_PKG
    d.app_start(IG_PKG)
    assert d.step == 4 and d.finished and d.app_current()["package"] == IG_PKG
    d.press("home")
    assert d.past_end == [{"kind": "press", "key": "home"}] and len(d.divergences) == 2


def test_replayed_selector_taps_match_by_selector_and_instance(tmp_path: Path) -> None:
    d: ReplayDevice = ReplayDevice(
        _tape(
            tmp_path,
            [
                {"screen": "a.xml", "after": []},
                {"screen": "b.xml", "after": [{"kind": "tap", "selector": {"text": "Open"}, "index": 1}]},
                {"screen": "a.xml", "after": [{"kind": "type", "selector": {"text": "Field"}}]},
            ],
        )
    )
    assert d.app_current()["package"] == IG_PKG  # read from the hierarchy when the tape doesn't say
    d(text="Open")[0].click()
    assert len(d.divergences) == 1 and d.step == 0
    with pytest.raises(LookupError):
        d(text="Missing").click()
    d(text="Open")[1].click()
    d(text="Field").set_text("x")
    assert d.step == 2 and d.typed == [(None, "x")]


@pytest.mark.parametrize(
    ("done", "taped", "same"),
    [
        ({"kind": "swipe", "path": [0, 9, 0, 1]}, {"kind": "swipe", "path": [0, 1, 0, 9]}, False),
        ({"kind": "swipe", "path": [9, 0, 1, 0]}, {"kind": "swipe", "path": [8, 1, 2, 1]}, True),
        ({"kind": "swipe", "path": [1, 0, 9, 0]}, {"kind": "swipe", "path": [9, 0, 1, 0]}, False),
        ({"kind": "click", "point": [1, 1]}, {"kind": "click", "point": []}, False),
        ({"kind": "press", "key": "back"}, {"kind": "press", "key": "home"}, False),
    ],
)
def test_actions_compare_by_what_they_do(done: recording.Action, taped: recording.Action, same: bool) -> None:
    assert same_action(done, taped) is same


@pytest.mark.parametrize(
    "text",
    [
        "[]",
        '{"steps": [3]}',
        '{"steps": [{"screen": "a.xml", "after": [3]}]}',
        '{"steps": [{"screen": "a.xml", "after": [{"kind": "shake"}]}]}',
    ],
)
def test_a_malformed_tape_is_refused(tmp_path: Path, text: str) -> None:
    (tmp_path / recording.TAPE_FILE).write_text(text)
    with pytest.raises(ValueError):
        load_tape(tmp_path)
