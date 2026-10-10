"""Recording a run as a tape (RECORD_DIR): each screen the scraper looked at, and the taps, key presses
and swipes that led from one to the next. tests/replaydevice.py plays a tape back as a device, so the
navigation code runs against a real run's sequence of screens.

A tape is a directory: `tape.json`, and one `NNNN.xml` hierarchy per step. A step is the screen that
was in front and the actions made since the step before; an empty list means the screen changed with
no action of the scraper's (a load finishing, an interstitial, the app leaving the foreground).
Hierarchies are real dumps, with real accounts and captions in them, and so is a copied link: a tape
stays out of the repository until it has been pseudonymized. Typed text is never recorded.

Recording costs device round trips the scraper wouldn't otherwise make (a dump before the first look
at each new screen; an action itself adds none), so it is for a development run, not for the deployed
scraper.
"""

import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from PIL import Image

from . import uidevice
from .common import log

# Seconds after which a screen is dumped again before it's looked at, in case it changed by itself.
STALE_SECONDS: float = 1.0
TAPE_FILE: str = "tape.json"

type ActionKind = Literal["click", "press", "swipe", "tap", "type", "start"]
type SelectorArgs = dict[str, str | list[str]]


class Action(TypedDict):
    kind: ActionKind
    key: NotRequired[str]  # press
    point: NotRequired[list[int]]  # click: x, y
    path: NotRequired[list[int]]  # swipe: from x, y to x, y
    selector: NotRequired[SelectorArgs]  # tap, type
    index: NotRequired[int]  # tap, type: the instance of a selector matching several nodes


class Step(TypedDict):
    screen: str  # the hierarchy's file name
    after: list[Action]
    clipboard: NotRequired[str | None]  # what the clipboard read as on this screen
    package: NotRequired[str]  # the app in front, when the scraper asked


class Recorder:
    """A Device that passes every call on to the real one and keeps a tape of the run."""

    def __init__(
        self, d: uidevice.Device, directory: Path, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._d: uidevice.Device = d
        self._dir: Path = directory
        self._clock: Callable[[], float] = clock
        self._steps: list[Step] = []
        self._pending: list[Action] = []
        self._last_xml: str | None = None
        self._seen_at: float = 0.0
        self._failed: bool = False
        directory.mkdir(parents=True, exist_ok=True)

    # --- the tape -----------------------------------------------------------------------------
    def _save_step(self, xml: str) -> None:
        """Add the screen in front as a step, unless nothing was done and nothing changed."""
        self._seen_at = self._clock()
        if xml == self._last_xml and not self._pending:
            return
        self._last_xml = xml
        if self._failed:
            return
        name: str = f"{len(self._steps):04d}.xml"
        self._steps.append({"screen": name, "after": self._pending})
        self._pending = []
        self._write(name, xml)

    def _write(self, name: str | None = None, xml: str = "") -> None:
        """Write the tape, and a step's hierarchy when one is given. A failed write stops the
        recording, not the run."""
        if self._failed:
            return
        try:
            if name:
                (self._dir / name).write_text(xml)
            (self._dir / TAPE_FILE).write_text(
                json.dumps({"version": 1, "steps": self._steps}, indent=1) + "\n"
            )
        except OSError as e:
            self._failed = True
            log("WARN: recording stopped, the tape could not be written:", repr(e))

    def look(self) -> None:
        """Called before the scraper reads the screen any way but a dump of its own: record the screen
        first if something was done since the last step, or it is old enough to have changed."""
        if self._pending or self._last_xml is None or self._clock() - self._seen_at > STALE_SECONDS:
            self._save_step(self._d.dump_hierarchy())

    def did(self, action: Action) -> None:
        self._pending.append(action)

    # --- Device -------------------------------------------------------------------------------
    def __call__(self, **kwargs: str | list[str]) -> uidevice.Selector:
        return _RecordedSelector(self, self._d(**kwargs), dict(kwargs), None)

    @property
    def info(self) -> Mapping[str, str | int | bool | None]:
        return self._d.info

    @property
    def clipboard(self) -> str | None:
        text: str | None = self._d.clipboard
        self.look()
        # a link once read on a screen stays that screen's: the scraper empties the clipboard before
        # the next Copy link, and that emptiness is its own doing, not the screen's
        if self._steps and (text or not self._steps[-1].get("clipboard")):
            self._steps[-1]["clipboard"] = text
            self._write()
        return text

    def set_clipboard(self, text: str, label: str | None = None) -> None:
        self._d.set_clipboard(text, label)

    def dump_hierarchy(self) -> str:
        xml: str = self._d.dump_hierarchy()
        self._save_step(xml)
        return xml

    def screenshot(self) -> Image.Image:
        return self._d.screenshot()

    def window_size(self) -> tuple[int, int]:
        return self._d.window_size()

    def implicitly_wait(self, seconds: float | None = None) -> float:
        return self._d.implicitly_wait(seconds)

    def app_list(self) -> list[str]:
        return self._d.app_list()

    def app_current(self) -> Mapping[str, str]:
        self.look()
        current: Mapping[str, str] = self._d.app_current()
        if self._steps and "package" in current:
            self._steps[-1]["package"] = current["package"]
            self._write()
        return current

    def app_start(self, package_name: str, activity: str | None = None, stop: bool = False) -> None:
        self._d.app_start(package_name, activity, stop)
        self.did({"kind": "start"})

    def shell(self, cmdargs: str | list[str], timeout: float = 60) -> uidevice.ShellOutput:
        return self._d.shell(cmdargs, timeout)

    def press(self, key: str) -> None:
        self._d.press(key)
        self.did({"kind": "press", "key": key})

    def swipe(self, fx: int, fy: int, tx: int, ty: int, duration: float | None = None) -> None:
        self._d.swipe(fx, fy, tx, ty, duration)
        self.did({"kind": "swipe", "path": [fx, fy, tx, ty]})

    def click(self, x: int, y: int) -> None:
        self._d.click(x, y)
        self.did({"kind": "click", "point": [x, y]})


class _RecordedSelector:
    def __init__(
        self,
        recorder: Recorder,
        selector: uidevice.Selector,
        kwargs: dict[str, str | list[str]],
        index: int | None,
    ) -> None:
        self._recorder: Recorder = recorder
        self._selector: uidevice.Selector = selector
        self._kwargs: dict[str, str | list[str]] = kwargs
        self._index: int | None = index

    def _action(self, kind: ActionKind) -> Action:
        action: Action = {"kind": kind, "selector": self._kwargs}
        if self._index is not None:
            action["index"] = self._index
        return action

    def exists(self, timeout: float = 0) -> bool:
        self._recorder.look()
        found: bool = self._selector.exists(timeout=timeout)
        if found and timeout:
            self._recorder.look()  # it may have appeared while this waited
        return found

    @property
    def count(self) -> int:
        self._recorder.look()
        return self._selector.count

    @property
    def info(self) -> Mapping[str, Mapping[str, int]]:
        self._recorder.look()
        return self._selector.info

    def __getitem__(self, instance: int) -> uidevice.Selector:
        return _RecordedSelector(self._recorder, self._selector[instance], self._kwargs, instance)

    def click(self, timeout: float | None = None) -> None:
        self._selector.click(timeout=timeout)
        self._recorder.did(self._action("tap"))

    def set_text(self, text: str, timeout: float | None = None) -> None:
        self._selector.set_text(text, timeout=timeout)
        self._recorder.did(self._action("type"))
