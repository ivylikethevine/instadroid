"""The hashed dependency locks stay in step. requirements-dev.txt is compiled with app/requirements.txt as a
constraint, so the tests run against the versions the image ships; Dependabot updates each lock on its own,
so a bump to one that the other hasn't picked up fails here instead of drifting silently."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)", re.MULTILINE)


def _pins(lock: Path) -> dict[str, str]:
    return {m.group(1).lower(): m.group(2) for m in PIN.finditer(lock.read_text())}


def test_every_app_pin_is_in_the_dev_lock_at_the_same_version() -> None:
    app = _pins(ROOT / "app" / "requirements.txt")
    dev = _pins(ROOT / "requirements-dev.txt")
    assert len(app) > 10
    mismatched = {name: (version, dev.get(name)) for name, version in app.items() if dev.get(name) != version}
    assert not mismatched, (
        "app/requirements.txt and requirements-dev.txt disagree (app, dev); regenerate requirements-dev.txt:\n"
        + "\n".join(f"{name}: {versions}" for name, versions in sorted(mismatched.items()))
    )


def test_every_lock_entry_is_hashed() -> None:
    for lock in (ROOT / "app" / "requirements.txt", ROOT / "requirements-dev.txt"):
        entries = re.split(r"\n(?=[A-Za-z0-9])", lock.read_text())
        unhashed = [
            entry.split()[0] for entry in entries if PIN.match(entry) and "--hash=sha256:" not in entry
        ]
        assert not unhashed, f"{lock.relative_to(ROOT)} has entries without hashes: {unhashed}"
