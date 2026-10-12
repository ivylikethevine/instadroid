"""Report which selector resource ids a new Instagram build still has, from `aapt2 dump resources`.

    aapt2 dump resources com.instagram.android.apk > 451.txt
    resource-id-report --build 451.0.0.40.1 --dump 451.txt
    resource-id-report --build 451.0.0.40.1 --dump 451.txt --old-build 450.0.0.50.77 --old-dump 450.txt

Prints a Markdown section for the new-builds issue: every resource id the covering profile's selectors
use that the build's base APK no longer declares, and, given an older build's dump, how many ids were
added and removed between the two. A removed selector id is near-certain drift. Text and content-desc
selectors aren't in the APK's resources and layout changes don't show up, so a clean report doesn't
replace a baseline on a device. Used by .github/scripts/resource_id_report.sh; never touches a device.
"""

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

import igprofiles
from igprofiles.base import Selectors, SelectorValue

_ID: re.Pattern[str] = re.compile(r"^\s*resource 0x[0-9a-f]+ id/(\S+)", re.MULTILINE)
_OWN: str = "com.instagram.android:id/"
LISTED: int = 40  # added or removed ids shown by name; the rest are counted


def declared_ids(dump: str) -> set[str]:
    """The names of the `id` resources in `aapt2 dump resources` output."""
    return {m[1] for m in _ID.finditer(dump)}


def selector_ids(selectors: Selectors) -> dict[str, tuple[str, ...]]:
    """{selector key: the Instagram resource ids it names}, for the keys matched by resource id. An id of
    another package ("android:id/list") isn't in Instagram's APK and is left out."""
    found: dict[str, tuple[str, ...]] = {}
    key: str
    value: SelectorValue
    for key, value in selectors.items():
        if key.endswith(("_id", "_ids")) and isinstance(value, (str, list, tuple)):
            names: list[str] = [value] if isinstance(value, str) else list(value)
            own: tuple[str, ...] = tuple(
                n.removeprefix(_OWN) for n in names if ":" not in n.removeprefix(_OWN)
            )
            if own:
                found[key] = own
    return found


def _named(names: set[str]) -> str:
    listed: list[str] = sorted(names)[:LISTED]
    more: str = f" and {len(names) - LISTED} more" if len(names) > LISTED else ""
    return ", ".join(f"`{n}`" for n in listed) + more


def report(build: str, ids: set[str], old_build: str | None = None, old_ids: set[str] | None = None) -> str:
    """The Markdown section for one build, or a line saying no profile covers it."""
    profile: str | None = igprofiles.covering(igprofiles.major_of(build))
    lines: list[str] = [f"### Resource ids in `{build}`", ""]
    if not profile:
        return "\n".join([*lines, "No profile covers this build, so there are no selectors to check.", ""])
    wanted: dict[str, tuple[str, ...]] = selector_ids(igprofiles.load(profile).selectors)
    missing: dict[str, list[str]] = {
        key: [n for n in names if n not in ids] for key, names in wanted.items() if not ids.issuperset(names)
    }
    total: int = sum(len(names) for names in wanted.values())
    if missing:
        lines += [
            f"{sum(len(gone) for gone in missing.values())} of the {total} resource ids `{profile}`'s"
            " selectors use are gone from the base APK, which almost certainly needs a profile fork:",
            "",
            *(f"- `{key}`: {', '.join(f'`{n}`' for n in gone)}" for key, gone in missing.items()),
        ]
    else:
        lines.append(f"All {total} resource ids `{profile}`'s selectors use are in the base APK.")
    if old_build and old_ids is not None:
        added: set[str] = ids - old_ids
        removed: set[str] = old_ids - ids
        lines += ["", f"Against `{old_build}`: {len(added)} ids added, {len(removed)} removed."]
        if removed:
            lines += ["", f"Removed: {_named(removed)}"]
        if added:
            lines += ["", f"Added: {_named(added)}"]
    lines += [
        "",
        "Text and content-desc selectors and layout changes don't show up here; only a baseline does.",
        "",
    ]
    return "\n".join(lines)


class Options(argparse.Namespace):
    build: str
    dump: Path
    old_build: str | None
    old_dump: Path | None


def main(argv: Sequence[str] | None = None) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--build", required=True, help="the build the dump is of, e.g. 451.0.0.40.1")
    ap.add_argument("--dump", required=True, type=Path, help="`aapt2 dump resources` of its base APK")
    ap.add_argument("--old-build", help="an older build to count added and removed ids against")
    ap.add_argument("--old-dump", type=Path, help="`aapt2 dump resources` of the older build's base APK")
    opts: Options = ap.parse_args(argv, namespace=Options())
    ids: set[str] = declared_ids(opts.dump.read_text())
    if not ids:
        print(f"no id resources in {opts.dump}; is it `aapt2 dump resources` output?", file=sys.stderr)
        return 1
    old_ids: set[str] | None = declared_ids(opts.old_dump.read_text()) if opts.old_dump else None
    if opts.old_build and not old_ids:
        print("--old-build needs --old-dump with id resources in it", file=sys.stderr)
        return 1
    sys.stdout.write(report(opts.build, ids, opts.old_build, old_ids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
