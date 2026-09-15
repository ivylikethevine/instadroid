"""Report Instagram builds on APKPure newer than anything validated with a version profile.

    apkeep -l -a com.instagram.android -d apk-pure | check-new-builds
    check-new-builds --versions-file versions.txt

Reads apkeep's version listing (comma or newline separated) and prints a Markdown issue body listing,
for each major version newer than the newest validated build, its newest build and the commands to
baseline it. Prints nothing when there's nothing new. Used by .github/workflows/new-builds.yml; never
downloads an APK or touches a device.
"""

import argparse
import re
import sys
from pathlib import Path

import igprofiles

_BUILD = re.compile(r"\b\d{3}\.\d+\.\d+\.\d+\.\d+\b")


def newer_builds(listing: str, newest_validated: str | None) -> dict[int, str]:
    """{major: newest build} for every major version in `listing` above the newest validated build's."""
    floor = igprofiles.major_of(newest_validated) or 0
    newest: dict[int, str] = {}
    for build in (m[0] for m in _BUILD.finditer(listing)):
        major = igprofiles.major_of(build) or 0
        if major > floor and (
            major not in newest or igprofiles.version_key(build) > igprofiles.version_key(newest[major])
        ):
            newest[major] = build
    return dict(sorted(newest.items()))


def issue_body(builds: dict[int, str], newest_validated: str | None) -> str:
    if not builds:
        return ""
    lines = [
        f"APKPure lists Instagram builds newer than the newest validated one ({newest_validated or 'none'}):",
        "",
        "| Major | Newest build | Covered by |",
        "|---|---|---|",
    ]
    for major, build in builds.items():
        lines.append(f"| {major} | `{build}` | `{igprofiles.covering(major) or 'no profile'}` |")
    first = next(iter(builds.values()))
    lines += [
        "",
        'Each still runs under the covering profile, with a "hasn\'t been validated" warning, until someone',
        'checks it on a device (docs/PROFILES.md, "Adding a version"):',
        "",
        "```bash",
        f"new-profile baseline {first}",
        f"new-profile promote {first}",
        f"new-profile validate {first}   # or: fork {first}, if check found drift",
        "```",
        "",
    ]
    return "\n".join(lines)


class Options(argparse.Namespace):
    versions_file: Path | None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--versions-file", type=Path, help="apkeep's listing (default: stdin)")
    opts = ap.parse_args(namespace=Options())
    listing = opts.versions_file.read_text() if opts.versions_file else sys.stdin.read()
    if not _BUILD.search(listing):
        print("no Instagram builds in the listing; did apkeep fail?", file=sys.stderr)
        return 1
    newest = igprofiles.newest_build()
    sys.stdout.write(issue_body(newer_builds(listing, newest), newest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
