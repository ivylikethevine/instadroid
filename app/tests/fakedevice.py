"""A scripted stand-in for a uiautomator2 device, driven by synthetic hierarchy XML.

Each screen is a hierarchy string built with node()/hierarchy(). Nodes may carry two attributes a
real dump never has (parse_hierarchy() ignores unknown attributes):

  goto="<screen>"  tapping the node switches to that screen ("" = the tap does nothing)
  clip="<text>"    tapping the node copies that text to the clipboard

Back presses and swipes use per-screen maps: `back`, `scroll` (swipe up), `pull` (swipe down) and
`hswipe` (horizontal). Screens in `foreign` are outside Instagram: app_current() reports the
launcher there, and app_start() brings Instagram back on `launch_screen`.

Only the slice of the uiautomator2 API the scraper actually uses is implemented.
"""

import re
import zlib
from collections.abc import Iterable

from lxml import etree
from PIL import Image

IG_PKG = "com.instagram.android"
LAUNCHER_PKG = "com.android.launcher3"
WIDTH, HEIGHT = 1080, 2340
_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")

type Node = tuple[dict[str, str], list[Node]]


def node(
    rid: str = "",
    cls: str = "android.view.View",
    text: str = "",
    desc: str = "",
    bounds: tuple[int, int, int, int] = (0, 0, WIDTH, 100),
    children: Iterable[Node] = (),
    **extra: str | None,
) -> Node:
    """One hierarchy node. A bare `rid` is expanded to Instagram's "<pkg>:id/<rid>" form."""
    if rid and ":" not in rid:
        rid = f"{IG_PKG}:id/{rid}"
    attrs = {
        "class": cls,
        "package": IG_PKG,
        "resource-id": rid,
        "text": text,
        "content-desc": desc,
        "bounds": "[{},{}][{},{}]".format(*bounds),
    }
    attrs.update({k: v for k, v in extra.items() if v is not None})
    return attrs, list(children)


def hierarchy(*children: Node) -> str:
    root = etree.Element("hierarchy", rotation="0")

    def add(parent: etree._Element, n: Node) -> None:
        attrs, kids = n
        el = etree.SubElement(parent, "node", attrs)
        for kid in kids:
            add(el, kid)

    add(root, node(cls="android.widget.FrameLayout", bounds=(0, 0, WIDTH, HEIGHT), children=children))
    return etree.tostring(root, encoding="unicode")


class Out:
    def __init__(self, output: str) -> None:
        self.output: str = output


def _bounds(n: etree._Element) -> tuple[int, int, int, int]:
    m = _BOUNDS.match(n.get("bounds") or "")
    if not m:
        return (0, 0, 0, 0)
    x1, y1, x2, y2 = map(int, m.groups())
    return (x1, y1, x2, y2)


def _area(n: etree._Element) -> int:
    x1, y1, x2, y2 = _bounds(n)
    return (x2 - x1) * (y2 - y1)


def _matches(n: etree._Element, key: str, value: str | list[str]) -> bool:
    if not isinstance(value, str):
        raise NotImplementedError(f"selector {key!r} with a list value")
    if key == "text":
        return (n.get("text") or "") == value
    if key == "textContains":
        return value in (n.get("text") or "")
    if key == "description":
        return (n.get("content-desc") or "") == value
    if key == "resourceIdMatches":
        return re.fullmatch(value, n.get("resource-id") or "") is not None
    if key == "className":
        return n.get("class") == value
    raise NotImplementedError(f"selector {key!r}")


class FakeSelector:
    def __init__(self, dev: FakeDevice, kw: dict[str, str | list[str]], index: int | None = None) -> None:
        self._dev, self._kw, self._index = dev, kw, index

    def _all(self) -> list[etree._Element]:
        return [n for n in self._dev.nodes() if all(_matches(n, k, v) for k, v in self._kw.items())]

    def _nodes(self) -> list[etree._Element]:
        found = self._all()
        return found if self._index is None else found[self._index : self._index + 1]

    def exists(self, timeout: float = 0) -> bool:
        return bool(self._nodes())

    @property
    def count(self) -> int:
        return len(self._all())

    def __getitem__(self, instance: int) -> FakeSelector:
        return FakeSelector(self._dev, self._kw, instance)

    def click(self, timeout: float | None = None) -> None:
        found = self._nodes()
        if not found:
            raise LookupError(f"no node matches {self._kw}")
        self._dev.tap(found[0])

    def set_text(self, text: str, timeout: float | None = None) -> None:
        self._dev.typed.append((self._index, text))

    @property
    def info(self) -> dict[str, dict[str, int]]:
        found = self._nodes()
        if not found:
            raise LookupError(f"no node matches {self._kw}")
        x1, y1, x2, y2 = _bounds(found[0])
        return {"bounds": {"left": x1, "top": y1, "right": x2, "bottom": y2}}


class FakeDevice:
    @property
    def info(self) -> dict[str, str | int | bool | None]:
        return {"productName": "redroid_x86_64"}

    def __init__(
        self,
        screens: dict[str, str],
        start: str,
        *,
        back: dict[str, str] | None = None,
        scroll: dict[str, str] | None = None,
        pull: dict[str, str] | None = None,
        hswipe: dict[str, str] | None = None,
        foreign: Iterable[str] = ("launcher",),
        launch_screen: str = "home",
        launch_blocked: bool = False,
        installed: Iterable[str] = (IG_PKG,),
        ig_version: str = "445.0.0.45.83",
    ) -> None:
        self.screens = {"launcher": hierarchy(), **screens}
        self.screen = start
        self.back, self.scroll, self.pull, self.hswipe = back or {}, scroll or {}, pull or {}, hswipe or {}
        self.foreign, self.launch_screen, self.launch_blocked = set(foreign), launch_screen, launch_blocked
        self.installed, self.ig_version = list(installed), ig_version
        self.props = {
            "ro.build.version.release": "13",
            "ro.build.version.sdk": "33",
            "ro.product.name": "redroid",
        }
        self.clipboard: str | None = ""
        self.history = [start]
        self.taps: list[tuple[int, int]] = []
        self.presses: list[str] = []
        self.swipes: list[tuple[int, int, int, int]] = []
        self.typed: list[tuple[int | None, str]] = []
        self.launches: list[str | None] = []
        self.shell_calls: list[str] = []

    # --- state --------------------------------------------------------------------------------
    def _go(self, screen: str | None) -> None:
        if screen:
            self.screen = screen
            self.history.append(screen)

    def nodes(self) -> list[etree._Element]:
        return list(etree.fromstring(self.screens[self.screen].encode()).iter("node"))

    def tap(self, n: etree._Element) -> None:
        if n.get("clip") is not None:
            self.clipboard = n.get("clip")
        self._go(n.get("goto") or None)

    # --- uiautomator2 API ---------------------------------------------------------------------
    def __call__(self, **kwargs: str | list[str]) -> FakeSelector:
        return FakeSelector(self, kwargs)

    def dump_hierarchy(self) -> str:
        return self.screens[self.screen]

    def screenshot(self) -> Image.Image:
        # A solid colour per screen: crops of different screens hash differently, same screen alike.
        c = zlib.crc32(self.screen.encode())
        return Image.new("RGB", (WIDTH, HEIGHT), (c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF))

    def window_size(self) -> tuple[int, int]:
        return WIDTH, HEIGHT

    def implicitly_wait(self, seconds: float | None = None) -> float:
        return 0.0 if seconds is None else seconds

    def app_list(self) -> list[str]:
        return list(self.installed)

    def install(self, pkg: str = IG_PKG) -> None:
        """Not part of the real uiautomator2 API; a test hook so a fake `adb install` (mocked at
        the subprocess level in scraper tests) can flip app_list() from absent to present."""
        if pkg not in self.installed:
            self.installed.append(pkg)

    def app_current(self) -> dict[str, str]:
        return {"package": LAUNCHER_PKG if self.screen in self.foreign else IG_PKG}

    def app_start(self, package_name: str, activity: str | None = None, stop: bool = False) -> None:
        self.launches.append(activity)
        if package_name == IG_PKG and self.screen in self.foreign and not self.launch_blocked:
            self._go(self.launch_screen)

    def shell(self, cmdargs: str | list[str], timeout: float = 60) -> Out:
        joined = " ".join(cmdargs) if isinstance(cmdargs, list) else cmdargs
        self.shell_calls.extend(joined.split("; "))  # one entry per command, as the device's sh runs them
        if "resolve-activity" in joined:
            return Out(f"priority=0 preferredOrder=0\n{IG_PKG}/com.instagram.mainactivity.LauncherActivity")
        if joined.startswith("dumpsys package"):
            return Out(f"  Package [{IG_PKG}]\n    versionName={self.ig_version}\n")
        if joined.startswith("getprop "):
            return Out(self.props.get(joined.split()[-1], ""))
        return Out("")

    def press(self, key: str) -> None:
        self.presses.append(key)
        if key == "home":
            self._go("launcher")
        elif key == "back":
            self._go(self.back.get(self.screen))

    def swipe(self, fx: int, fy: int, tx: int, ty: int, duration: float | None = None) -> None:
        self.swipes.append((fx, fy, tx, ty))
        if abs(tx - fx) > abs(ty - fy):
            self._go(self.hswipe.get(self.screen))
        elif ty < fy:
            self._go(self.scroll.get(self.screen))
        else:
            self._go(self.pull.get(self.screen))

    def click(self, x: int, y: int) -> None:
        """Coordinate tap: the smallest node under the point that does something, else the smallest."""
        self.taps.append((x, y))
        hits = [n for n in self.nodes() if _contains(n, x, y)]
        actionable = [n for n in hits if n.get("goto") is not None or n.get("clip") is not None]
        pool = actionable or hits
        if pool:
            self.tap(min(pool, key=_area))


def _contains(n: etree._Element, x: int, y: int) -> bool:
    x1, y1, x2, y2 = _bounds(n)
    return x1 <= x <= x2 and y1 <= y <= y2
