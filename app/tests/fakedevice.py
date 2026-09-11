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

from lxml import etree
from PIL import Image

IG_PKG = "com.instagram.android"
LAUNCHER_PKG = "com.android.launcher3"
WIDTH, HEIGHT = 1080, 2340
_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def node(rid="", cls="android.view.View", text="", desc="", bounds=(0, 0, WIDTH, 100), children=(), **extra):
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


def hierarchy(*children) -> str:
    root = etree.Element("hierarchy", rotation="0")

    def add(parent, n):
        attrs, kids = n
        el = etree.SubElement(parent, "node", attrs)
        for kid in kids:
            add(el, kid)

    add(root, node(cls="android.widget.FrameLayout", bounds=(0, 0, WIDTH, HEIGHT), children=children))
    return etree.tostring(root, encoding="unicode")


class Out:
    def __init__(self, output):
        self.output = output


def _bounds(n):
    m = _BOUNDS.match(n.get("bounds") or "")
    return tuple(map(int, m.groups())) if m else (0, 0, 0, 0)


def _area(n):
    x1, y1, x2, y2 = _bounds(n)
    return (x2 - x1) * (y2 - y1)


def _matches(n, key, value) -> bool:
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
    def __init__(self, dev, kw, index=None):
        self._dev, self._kw, self._index = dev, kw, index

    def _all(self):
        return [n for n in self._dev.nodes() if all(_matches(n, k, v) for k, v in self._kw.items())]

    def _nodes(self):
        found = self._all()
        return found if self._index is None else found[self._index : self._index + 1]

    def exists(self, timeout=0):
        return bool(self._nodes())

    @property
    def count(self):
        return len(self._all())

    def __getitem__(self, i):
        return FakeSelector(self._dev, self._kw, i)

    def click(self):
        found = self._nodes()
        if not found:
            raise LookupError(f"no node matches {self._kw}")
        self._dev.tap(found[0])

    def set_text(self, text):
        self._dev.typed.append((self._index, text))

    @property
    def info(self):
        found = self._nodes()
        if not found:
            raise LookupError(f"no node matches {self._kw}")
        x1, y1, x2, y2 = _bounds(found[0])
        return {"bounds": {"left": x1, "top": y1, "right": x2, "bottom": y2}}


class FakeDevice:
    info = {"productName": "redroid_x86_64"}

    def __init__(
        self,
        screens: dict,
        start: str,
        *,
        back=None,
        scroll=None,
        pull=None,
        hswipe=None,
        foreign=("launcher",),
        launch_screen="home",
        launch_blocked=False,
        installed=(IG_PKG,),
        ig_version="445.0.0.45.83",
    ):
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
        self.clipboard = ""
        self.history = [start]
        self.taps, self.presses, self.swipes, self.typed, self.launches = [], [], [], [], []
        self.shell_calls = []

    # --- state --------------------------------------------------------------------------------
    def _go(self, screen):
        if screen:
            self.screen = screen
            self.history.append(screen)

    def nodes(self):
        return list(etree.fromstring(self.screens[self.screen].encode()).iter("node"))

    def tap(self, n):
        if n.get("clip") is not None:
            self.clipboard = n.get("clip")
        self._go(n.get("goto") or None)

    # --- uiautomator2 API ---------------------------------------------------------------------
    def __call__(self, **kw):
        return FakeSelector(self, kw)

    def dump_hierarchy(self):
        return self.screens[self.screen]

    def screenshot(self):
        # A solid colour per screen: crops of different screens hash differently, same screen alike.
        c = zlib.crc32(self.screen.encode())
        return Image.new("RGB", (WIDTH, HEIGHT), (c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF))

    def window_size(self):
        return WIDTH, HEIGHT

    def implicitly_wait(self, seconds):
        pass

    def app_list(self):
        return list(self.installed)

    def app_current(self):
        return {"package": LAUNCHER_PKG if self.screen in self.foreign else IG_PKG}

    def app_start(self, pkg, activity=None, stop=False):
        self.launches.append(activity)
        if pkg == IG_PKG and self.screen in self.foreign and not self.launch_blocked:
            self._go(self.launch_screen)

    def shell(self, cmd):
        joined = " ".join(cmd) if isinstance(cmd, list) else cmd
        self.shell_calls.append(joined)
        if "resolve-activity" in joined:
            return Out(f"priority=0 preferredOrder=0\n{IG_PKG}/com.instagram.mainactivity.LauncherActivity")
        if joined.startswith("dumpsys package"):
            return Out(f"  Package [{IG_PKG}]\n    versionName={self.ig_version}\n")
        if joined.startswith("getprop "):
            return Out(self.props.get(joined.split()[-1], ""))
        return Out("")

    def press(self, key):
        self.presses.append(key)
        if key == "home":
            self._go("launcher")
        elif key == "back":
            self._go(self.back.get(self.screen))

    def swipe(self, x1, y1, x2, y2, duration=None):
        self.swipes.append((x1, y1, x2, y2))
        if abs(x2 - x1) > abs(y2 - y1):
            self._go(self.hswipe.get(self.screen))
        elif y2 < y1:
            self._go(self.scroll.get(self.screen))
        else:
            self._go(self.pull.get(self.screen))

    def click(self, x, y):
        """Coordinate tap: the smallest node under the point that does something, else the smallest."""
        self.taps.append((x, y))
        hits = [n for n in self.nodes() if _contains(n, x, y)]
        actionable = [n for n in hits if n.get("goto") is not None or n.get("clip") is not None]
        pool = actionable or hits
        if pool:
            self.tap(min(pool, key=_area))


def _contains(n, x, y) -> bool:
    x1, y1, x2, y2 = _bounds(n)
    return x1 <= x <= x2 and y1 <= y <= y2
