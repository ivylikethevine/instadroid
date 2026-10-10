"""Recognising a story that only reshares a stored post, by finding the post's image in the frame."""

import heapq
import math
import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import NamedTuple, Protocol

from PIL import Image, ImageChops, ImageFile, ImageFilter, ImageStat
from shared import sqlrows

from . import config, retention
from .common import log

# The reshare sticker is centred across the frame at any height, by default 88% of the frame wide;
# these are the card widths searched, as shares of the frame's.
SCALES: tuple[float, ...] = tuple(0.70 + 0.03 * i for i in range(11))
# Width in pixels a post image is shrunk to for the search over every scale and height.
COARSE_WIDTH: int = 24
# Grey levels two search pixels may differ by and still agree.
COARSE_TOLERANCE: int = 24
# Search placements, best first, that get the closer check.
CANDIDATES: int = 5
# Width in pixels of the thumbnails that closer check correlates.
FINE_WIDTH: int = 32
# Correlation from which a card is the post.
MATCH: float = 0.82
# Grey-level spread below which a post image is too featureless to tell from a story's backdrop.
FLAT: float = 8.0
# Rows left out above and below the card, as shares of the frame's width: the card's own edge above,
# the sticker's attribution line below.
MARGIN_ABOVE: float = 0.02
MARGIN_BELOW: float = 0.10
# How far above the matched image the sticker's top edge is looked for, as a share of the frame's
# width (a stored post's image can start below it, and one style of sticker has a header there), and
# the share of a row's pixels that has to be sharp for the row to be that edge.
EDGE_REACH: float = 0.08
EDGE_ROW: float = 0.35
# Edge-filter response from which a pixel counts as sharp; a reshare's backdrop is a blur.
SHARP: int = 30
# Share of sharp pixels around the card from which the story has something of its own.
PLAIN: float = 0.01


class _Resizable(Protocol):
    """Image.resize() as this module calls it. Pillow annotates `size` as also accepting a numpy array,
    a type left unknown when numpy isn't installed, so the method is read through this instead."""

    def resize(
        self,
        size: tuple[int, int],
        resample: int | None = None,
        box: tuple[float, float, float, float] | None = None,
    ) -> Image.Image: ...


def _shrunk(
    img: _Resizable, size: tuple[int, int], box: tuple[float, float, float, float] | None = None
) -> Image.Image:
    """`img`, or its `box`, averaged down to `size`."""
    return img.resize(size, Image.Resampling.BOX, box)


class _Mappable(Protocol):
    """Image.point() with a lookup table, read through this for the same reason."""

    def point(self, lut: Sequence[int]) -> Image.Image: ...


def _mapped(img: _Mappable, lut: Sequence[int]) -> Image.Image:
    return img.point(lut)


class Placement(NamedTuple):
    score: float
    scale: float  # the card's width, as a share of the frame's
    top: float  # the card's top edge, as a share of the frame's width


class PostImage(NamedTuple):
    post_id: str
    coarse: Image.Image
    fine: Image.Image


def post_image(post_id: str, img: Image.Image) -> PostImage | None:
    """The thumbnails a post's image is searched for by, or None for one too featureless to place."""
    grey: Image.Image = img.convert("L")
    coarse: Image.Image = _shrunk(
        grey, (COARSE_WIDTH, max(round(COARSE_WIDTH * grey.height / grey.width), 1))
    )
    if ImageStat.Stat(coarse).stddev[0] < FLAT:
        return None
    return PostImage(
        post_id, coarse, _shrunk(grey, (FINE_WIDTH, max(round(FINE_WIDTH * grey.height / grey.width), 1)))
    )


def _rows(img: Image.Image) -> list[bytes]:
    data: bytes = img.tobytes()
    return [data[i : i + img.width] for i in range(0, len(data), img.width)]


def _correlation(a: bytes, b: bytes) -> float:
    """Normalised cross-correlation of two equally long runs of grey levels."""
    n: int = len(a)
    mean_a: float = sum(a) / n
    mean_b: float = sum(b) / n
    spread_a: float = sum(x * x for x in a) - n * mean_a * mean_a
    spread_b: float = sum(y * y for y in b) - n * mean_b * mean_b
    if spread_a <= 0 or spread_b <= 0:
        return 0.0
    return (sum(x * y for x, y in zip(a, b, strict=True)) - n * mean_a * mean_b) / math.sqrt(
        spread_a * spread_b
    )


class Frame:
    """A story frame, prepared once for searching every stored post image in."""

    def __init__(self, img: Image.Image) -> None:
        self.grey: Image.Image = img.convert("L")
        width: int = self.grey.width
        height: int = self.grey.height
        # per scale, the frame's centre band of that width shrunk to COARSE_WIDTH, as rows
        self._bands: list[list[bytes]] = []
        scale: float
        for scale in SCALES:
            left: float = (1 - scale) / 2 * width
            band_height: int = round(COARSE_WIDTH * height / (scale * width))
            self._bands.append(
                _rows(_shrunk(self.grey, (COARSE_WIDTH, band_height), (left, 0, width - left, height)))
            )
        self._stacks: dict[tuple[int, int], Image.Image] = {}

    def _stack(self, band: int, rows: int) -> Image.Image:
        """Every `rows`-tall window of a band, top to bottom, laid side by side."""
        key: tuple[int, int] = (band, rows)
        if key not in self._stacks:
            lines: list[bytes] = self._bands[band]
            shifts: int = len(lines) - rows + 1
            self._stacks[key] = Image.frombytes(
                "L",
                (COARSE_WIDTH * shifts, rows),
                b"".join(b"".join(lines[y + r] for y in range(shifts)) for r in range(rows)),
            )
        return self._stacks[key]

    def search(self, coarse: Image.Image) -> Placement | None:
        """Where a post image sits best as a centred card: the share of pixels that agree, over every
        scale and height. None when it fits the frame at no scale."""
        best: Placement | None = None
        lines: list[bytes] = _rows(coarse)
        band: int
        scale: float
        for band, scale in enumerate(SCALES):
            shifts: int = len(self._bands[band]) - coarse.height + 1
            if shifts < 1:
                continue
            tiled: Image.Image = Image.frombytes(
                "L", (COARSE_WIDTH * shifts, coarse.height), b"".join(line * shifts for line in lines)
            )
            agree: Image.Image = _mapped(
                ImageChops.difference(self._stack(band, coarse.height), tiled), _AGREES
            )
            shares: bytes = _shrunk(agree, (shifts, 1)).tobytes()
            y: int = max(range(shifts), key=shares.__getitem__)
            if best is None or shares[y] / 255 > best.score:
                best = Placement(shares[y] / 255, scale, y / COARSE_WIDTH * scale)
        return best

    def refine(self, fine: Image.Image, near: Placement) -> Placement:
        """The best correlation of a post image with the frame around a search placement, in two
        passes, the second in smaller steps around the first's best."""
        width: int = self.grey.width
        want: bytes = fine.tobytes()
        best: Placement = Placement(-1.0, near.scale, near.top)
        scale_step: float
        top_step: float
        reach: int
        for scale_step, top_step, reach in ((0.0075, 0.01, 2), (0.005, 0.0025, 3)):
            around: Placement = best
            i: int
            for i in range(-reach, reach + 1):
                scale: float = min(around.scale + i * scale_step, 1.0)
                card_width: float = scale * width
                card_height: float = card_width * fine.height / fine.width
                left: float = (width - card_width) / 2
                j: int
                for j in range(-reach, reach + 1):
                    top: float = (around.top + j * top_step) * width
                    if top < 0 or top + card_height > self.grey.height:
                        continue
                    card: Image.Image = _shrunk(
                        self.grey, fine.size, (left, top, left + card_width, top + card_height)
                    )
                    score: float = _correlation(card.tobytes(), want)
                    if score > best.score:
                        best = Placement(score, scale, top / width)
        return best

    def plain_around(self, card: Placement, aspect: float) -> bool:
        """Whether the frame above and below a card (and its margins) is only backdrop: text, a sticker
        or a second picture there is sharp where the backdrop is a blur."""
        half: Image.Image = _shrunk(self.grey, (self.grey.width // 2, self.grey.height // 2))
        edges: Image.Image = half.filter(ImageFilter.FIND_EDGES)
        unit: float = self.grey.width / 2
        top: int = round(card.top * unit)
        above: int = top - round(MARGIN_ABOVE * unit)
        left: int = round((1 - card.scale) / 2 * half.width) + 4
        y: int
        for y in range(max(top - round(EDGE_REACH * unit), 0), top):
            row: Image.Image = edges.crop((left, y, half.width - left, y + 1))
            if sum(row.histogram()[SHARP + 1 :]) >= EDGE_ROW * row.width:
                above = y - 2
                break
        below: int = round((card.top + card.scale * aspect + MARGIN_BELOW) * unit)
        start: int
        end: int
        # the filter leaves the outermost pixels as they were, so they are left out
        for start, end in ((2, above), (below, edges.height - 2)):
            if end - start < 8:
                continue
            strip: Image.Image = edges.crop((2, start, edges.width - 2, end))
            if sum(strip.histogram()[SHARP + 1 :]) >= PLAIN * strip.width * strip.height:
                return False
        return True


_AGREES: list[int] = [255 if level <= COARSE_TOLERANCE else 0 for level in range(256)]


def find_reshared(frame: Frame, posts: Sequence[PostImage]) -> tuple[PostImage, Placement] | None:
    """The stored post image a story frame shows as a card, and where, or None."""
    placed: list[tuple[float, int, Placement]] = []
    index: int
    post: PostImage
    for index, post in enumerate(posts):
        near: Placement | None
        if near := frame.search(post.coarse):
            placed.append((near.score, index, near))
    best: tuple[PostImage, Placement] | None = None
    for _, index, near in heapq.nlargest(CANDIDATES, placed):
        exact: Placement = frame.refine(posts[index].fine, near)
        if exact.score >= MATCH and (best is None or exact.score > best[1].score):
            best = (posts[index], exact)
    return best


def _post_images(con: sqlite3.Connection) -> Iterator[PostImage]:
    """Every stored post's cover and slides that can still be read."""
    rows: list[sqlite3.Row] = sqlrows.fetch_all(
        con.execute(
            "SELECT id, media_file FROM posts WHERE media_file IS NOT NULL"
            " UNION ALL SELECT post_id, file FROM media"
        )
    )
    row: sqlite3.Row
    for row in rows:
        path: Path = config.MEDIA_DIR / sqlrows.must_str(row, 1)
        try:
            img: ImageFile.ImageFile
            with Image.open(path) as img:
                post: PostImage | None = post_image(sqlrows.must_str(row, 0), img)
        except OSError:  # pruned from disk, or not an image any more
            continue
        if post:
            yield post


def drop_reshared_stories(con: sqlite3.Connection, since: str) -> int:
    """Delete each story stored from `since` on that is a stored post's image as a card with nothing
    added around it: the posts feed already has that entry. A reshare with text of its own stays.
    Returns how many were deleted."""
    stories: list[sqlite3.Row] = sqlrows.fetch_all(
        con.execute("SELECT id, username, media_file FROM stories WHERE scraped_at >= ?", (since,))
    )
    if not stories:
        return 0
    posts: list[PostImage] = list(_post_images(con))
    dropped: int = 0
    row: sqlite3.Row
    for row in stories:
        media_file: str | None = sqlrows.cell_str(row, 2)
        if not media_file:
            continue
        try:
            img: ImageFile.ImageFile
            with Image.open(config.MEDIA_DIR / media_file) as img:
                frame: Frame = Frame(img)
        except OSError:
            continue
        found: tuple[PostImage, Placement] | None = find_reshared(frame, posts)
        if not found:
            continue
        fine: Image.Image = found[0].fine
        if not frame.plain_around(found[1], fine.height / fine.width):
            continue
        con.execute("DELETE FROM stories WHERE id = ?", (sqlrows.must_str(row, 0),))
        con.commit()
        retention.discard_media(media_file)
        dropped += 1
        log(f"story for {sqlrows.must_str(row, 1)}: only reshares stored post {found[0].post_id}; discarding")
    return dropped
