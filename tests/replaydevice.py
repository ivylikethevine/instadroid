"""A recorded run played back as a device: the tape a recording.Recorder wrote (see
app/instadroid/recording.py), stepped through as the code under test repeats the run's actions.

The screen in front is one step of the tape, and selectors read its hierarchy as FakeDevice does. An
action that matches the next one the tape expects moves towards the step it led to; any other action
changes nothing and is kept in `divergences`, which a test asserts is empty (`past_end` holds the
actions made once the tape has run out). A step recorded with no
action before it (the screen changed by itself) is entered by waiting: a pause (tick()), or a selector
wait that finds nothing on the screen in front.

A click matches a recorded click within NEAR pixels, since taps are jittered; a swipe matches one in the
same direction; a selector tap, one with the same selector; a key press, the same key.
"""

from pathlib import Path

from devtools import jsonvalues
from devtools.jsonvalues import JSON
from instadroid import recording
from lxml import etree

from tests.fakedevice import IG_PKG, LAUNCHER_PKG, FakeDevice, FakeSelector

NEAR: int = 120


def load_tape(directory: Path) -> list[recording.Step]:
    """The steps of a tape directory, checked as they are read."""
    raw: JSON = jsonvalues.loads((directory / recording.TAPE_FILE).read_text())
    steps: JSON = raw.get("steps") if isinstance(raw, dict) else None
    if not isinstance(steps, list):
        raise ValueError(f"{directory} holds no tape")
    tape: list[recording.Step] = []
    entry: JSON
    for entry in steps:
        screen: JSON = entry.get("screen") if isinstance(entry, dict) else None
        if not isinstance(entry, dict) or not isinstance(screen, str):
            raise ValueError(f"a step without a screen in {directory}")
        after: JSON = entry.get("after")
        step: recording.Step = {
            "screen": screen,
            "after": [_action(a) for a in after] if isinstance(after, list) else [],
        }
        clipboard: JSON = entry.get("clipboard")
        if "clipboard" in entry and (clipboard is None or isinstance(clipboard, str)):
            step["clipboard"] = clipboard
        package: JSON = entry.get("package")
        if isinstance(package, str):
            step["package"] = package
        tape.append(step)
    return tape


def _ints(value: JSON) -> list[int]:
    return [v for v in value if isinstance(v, int)] if isinstance(value, list) else []


def _action(raw: JSON) -> recording.Action:
    if not isinstance(raw, dict):
        raise ValueError(f"not an action: {raw!r}")
    kind: JSON = raw.get("kind")
    action: recording.Action
    if kind == "press":
        action = {"kind": "press", "key": str(raw.get("key"))}
    elif kind == "click":
        action = {"kind": "click", "point": _ints(raw.get("point"))}
    elif kind == "swipe":
        action = {"kind": "swipe", "path": _ints(raw.get("path"))}
    elif kind == "tap" or kind == "type":
        selector: JSON = raw.get("selector")
        args: recording.SelectorArgs = {}
        if isinstance(selector, dict):
            args.update({k: v for k, v in selector.items() if isinstance(v, str)})
        action = {"kind": "tap" if kind == "tap" else "type", "selector": args}
        index: JSON = raw.get("index")
        if isinstance(index, int):
            action["index"] = index
    elif kind == "start":
        action = {"kind": "start"}
    else:
        raise ValueError(f"unknown action kind {kind!r}")
    return action


def _direction(path: list[int]) -> str:
    fx: int
    fy: int
    tx: int
    ty: int
    fx, fy, tx, ty = path
    if abs(tx - fx) > abs(ty - fy):
        return "left" if tx < fx else "right"
    return "up" if ty < fy else "down"


def same_action(done: recording.Action, taped: recording.Action) -> bool:
    """Whether an action made in replay is the one the tape has next."""
    if done["kind"] != taped["kind"]:
        return False
    if done["kind"] == "press":
        return done.get("key") == taped.get("key")
    if done["kind"] == "click":
        a: list[int] = done.get("point", [])
        b: list[int] = taped.get("point", [])
        return len(a) == 2 and len(b) == 2 and abs(a[0] - b[0]) <= NEAR and abs(a[1] - b[1]) <= NEAR
    if done["kind"] == "swipe":
        return _direction(done.get("path", [0, 0, 0, 0])) == _direction(taped.get("path", [0, 0, 0, 0]))
    if done["kind"] == "start":
        return True
    return done.get("selector") == taped.get("selector") and done.get("index") == taped.get("index")


class ReplaySelector(FakeSelector):
    def __init__(self, dev: ReplayDevice, kw: dict[str, str | list[str]], index: int | None = None) -> None:
        super().__init__(dev, kw, index)
        self._replay: ReplayDevice = dev

    def _action(self, kind: recording.ActionKind) -> recording.Action:
        action: recording.Action = {"kind": kind, "selector": dict(self._kw)}
        if self._index is not None:
            action["index"] = self._index
        return action

    def exists(self, timeout: float = 0) -> bool:
        found: bool = super().exists(timeout)
        while not found and timeout and self._replay.tick():
            found = super().exists(timeout)
        return found

    def __getitem__(self, instance: int) -> ReplaySelector:
        return ReplaySelector(self._replay, self._kw, instance)

    def click(self, timeout: float | None = None) -> None:
        if not self._nodes():
            raise LookupError(f"no node matches {self._kw}")
        self._replay.did(self._action("tap"))

    def set_text(self, text: str, timeout: float | None = None) -> None:
        super().set_text(text, timeout)
        self._replay.did(self._action("type"))


class ReplayDevice(FakeDevice):
    def __init__(self, directory: Path, ig_version: str = "445.0.0.45.83") -> None:
        self.tape: list[recording.Step] = load_tape(directory)
        screens: dict[str, str] = {
            step["screen"]: (directory / step["screen"]).read_text() for step in self.tape
        }
        super().__init__(screens, self.tape[0]["screen"], ig_version=ig_version)
        self.step: int = 0
        self.done: int = 0  # how many of the next acted-on step's actions have been made
        self.divergences: list[tuple[int, recording.Action]] = []
        self.past_end: list[recording.Action] = []

    # --- the tape -----------------------------------------------------------------------------
    def _enter(self, step: int) -> None:
        self.step, self.done = step, 0
        entered: recording.Step = self.tape[step]
        self._go(entered["screen"])
        if "clipboard" in entered:
            self.clipboard = entered["clipboard"]

    def tick(self) -> bool:
        """Time passing: move to the next step if it was recorded with no action before it."""
        following: int = self.step + 1
        if self.done or following >= len(self.tape) or self.tape[following]["after"]:
            return False
        self._enter(following)
        return True

    def did(self, action: recording.Action) -> None:
        """An action of the code under test: one more of those the next acted-on step is waiting for,
        entering that step with the last of them, or a divergence."""
        target: int = next((i for i in range(self.step + 1, len(self.tape)) if self.tape[i]["after"]), -1)
        if target < 0:  # the last actions of a run are followed by no look at the screen, so by no step
            self.past_end.append(action)
            return
        if not same_action(action, self.tape[target]["after"][self.done]):
            self.divergences.append((self.step, action))
            return
        self.done += 1
        if self.done == len(self.tape[target]["after"]):
            self._enter(target)

    @property
    def finished(self) -> bool:
        """Whether no step that an action leads to is left."""
        return not any(step["after"] for step in self.tape[self.step + 1 :])

    # --- uiautomator2 API ---------------------------------------------------------------------
    def __call__(self, **kwargs: str | list[str]) -> ReplaySelector:
        return ReplaySelector(self, kwargs)

    def app_current(self) -> dict[str, str]:
        current: recording.Step = self.tape[self.step]
        if "package" in current:
            return {"package": current["package"]}
        root: etree._Element = etree.fromstring(self.screens[self.screen].encode())
        packages: set[str] = {n.get("package") or "" for n in root.iter("node")}
        return {"package": IG_PKG if IG_PKG in packages else LAUNCHER_PKG}

    def app_start(self, package_name: str, activity: str | None = None, stop: bool = False) -> None:
        self.launches.append(activity)
        self.did({"kind": "start"})

    def press(self, key: str) -> None:
        self.presses.append(key)
        self.did({"kind": "press", "key": key})

    def swipe(self, fx: int, fy: int, tx: int, ty: int, duration: float | None = None) -> None:
        self.swipes.append((fx, fy, tx, ty))
        self.did({"kind": "swipe", "path": [fx, fy, tx, ty]})

    def click(self, x: int, y: int) -> None:
        self.taps.append((x, y))
        self.did({"kind": "click", "point": [x, y]})
