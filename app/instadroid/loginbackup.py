"""The login alone: Instagram's app data, copied off the device and back, so a fresh /data volume (another
Android version's included) or a reset /data/system gets the logged-in session without a new login.

    scraper.py login-backup            BACKUP_DIR/login-<utc>.tar.gz, and a .json of what it came from
    scraper.py login-restore [FILE]    the newest backup, or FILE, into the installed Instagram

Both go over the adb CLI as root (this image's adbd restarts as root on `adb root`), with Instagram
force-stopped, and refuse unless the manual lock holds the scraper back. The archive leaves out the
caches and the `lib` link, which the installed APK supplies. A restore moves the app's current data
aside on the device rather than deleting it, extracts the archive, hands every file to the uid this
install was given and relabels it for SELinux. Whether Instagram accepts the copied session (it may
tie it to the keystore or a device id) only a device run can show."""

import gzip
import json
import shutil
import subprocess
import tarfile
import time
from datetime import UTC, datetime
from io import BufferedReader, BufferedWriter
from pathlib import Path
from typing import IO

from igprofiles import version_key
from shared.jsonvalues import JSON, loads

from . import config, control
from .common import log
from .device import parse_version_name

_NAME: str = "login-%Y%m%dT%H%M%SZ.tar.gz"
DATA_DIR: str = "/data/data"
# Rebuilt by Android or the APK: caches, and `lib`, a link into the APK's install path.
EXCLUDE: tuple[str, ...] = ("cache", "code_cache", "lib")
DEVICE_TMP: str = "/data/local/tmp"
ADB_TIMEOUT: float = 60.0
COPY_TIMEOUT: float = 1800.0  # an archive of a few hundred MB over a loopback adb


class LoginBackupError(RuntimeError):
    """A backup or restore that stopped, with why; nothing on the device was changed past that point."""


def _adb(args: list[str], *, out: IO[bytes] | None = None, timeout: float = ADB_TIMEOUT) -> str:
    """`adb -s ADB_ADDR <args>`: its stdout as text, or written to `out` as bytes. LoginBackupError when
    adb can't run, hangs or exits non-zero."""
    try:
        done: subprocess.CompletedProcess[bytes] = subprocess.run(
            ["adb", "-s", config.ADB_ADDR, *args],
            stdout=out if out is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise LoginBackupError(f"adb {' '.join(args)}: {e}") from None
    if done.returncode != 0:
        raise LoginBackupError(
            f"adb {' '.join(args)} exited {done.returncode}: {done.stderr.decode(errors='replace').strip()}"
        )
    return "" if out is not None else done.stdout.decode(errors="replace")


def _shell(command: str, timeout: float = ADB_TIMEOUT) -> str:
    """One root shell command on the device; its output, stripped."""
    return _adb(["shell", command], timeout=timeout).strip()


def _connect() -> None:
    """`adb connect ADB_ADDR`, whatever it answers: the shell command after it tells."""
    _: subprocess.CompletedProcess[bytes] = subprocess.run(
        ["adb", "connect", config.ADB_ADDR], capture_output=True, timeout=15, check=False
    )


def _as_root(attempts: int = 10, pause: float = 3.0) -> None:
    """adbd as root, which a fresh container's adbd isn't until asked. `adb root` restarts adbd, which
    drops the TCP transport, so it is connected again until the shell answers as root."""
    _adb(["root"])
    uid: str = ""
    attempt: int
    for attempt in range(attempts):
        if attempt:
            time.sleep(pause)
        try:
            _connect()
            uid = _shell("id -u")
        except OSError, subprocess.TimeoutExpired, LoginBackupError:
            continue
        if uid == "0":
            return
    raise LoginBackupError(
        f"adb root left the shell as uid {uid or '?'}; this needs an image whose adbd runs as root"
    )


def _installed_version() -> str:
    """The installed Instagram's versionName; LoginBackupError when it isn't installed."""
    version: str | None = parse_version_name(_shell(f"dumpsys package {config.IG_PKG}"))
    if not version:
        raise LoginBackupError(f"{config.IG_PKG} isn't installed: install it first (scraper.py install)")
    return version


def _prepare() -> str:
    """The checks both directions share, then root and Instagram stopped; the installed version."""
    if not control.locked():
        raise LoginBackupError(
            "the manual lock isn't held: run `scraper.py lock` first, so no run starts meanwhile"
        )
    _as_root()
    version: str = _installed_version()
    _shell(f"am force-stop {config.IG_PKG}")
    return version


def backups() -> list[Path]:
    """Login backups in BACKUP_DIR, oldest first (the names are fixed-width UTC times)."""
    directory: Path = Path(config.BACKUP_DIR)
    return sorted(directory.glob("login-*.tar.gz")) if directory.is_dir() else []


def _metadata(archive: Path) -> Path:
    return archive.with_name(archive.name.removesuffix(".tar.gz") + ".json")


def backup_login(now: datetime | None = None) -> Path:
    """Copy Instagram's app data to BACKUP_DIR/login-<utc>.tar.gz (owner-only: it is a logged-in
    session), with a .json beside it naming the build and Android release it came from."""
    version: str = _prepare()
    directory: Path = Path(config.BACKUP_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    dest: Path = directory / (now or datetime.now(UTC)).strftime(_NAME)
    partial: Path = dest.with_name(dest.name + ".part")
    excludes: str = " ".join(f"--exclude={config.IG_PKG}/{name}" for name in EXCLUDE)
    raw: Path = dest.with_name(dest.name + ".tar.part")
    out: BufferedWriter
    src: BufferedReader
    gz: gzip.GzipFile
    try:
        with raw.open("wb") as out:
            raw.chmod(0o600)
            _adb(
                ["exec-out", f"tar -cf - -C {DATA_DIR} {excludes} {config.IG_PKG}"],
                out=out,
                timeout=COPY_TIMEOUT,
            )
        _check_archive(raw)
        with raw.open("rb") as src, gzip.GzipFile(partial, "wb") as gz:
            partial.chmod(0o600)
            shutil.copyfileobj(src, gz)
    finally:
        raw.unlink(missing_ok=True)
    partial.replace(dest)
    meta: dict[str, str] = {
        "instagram": version,
        "android_release": _shell("getprop ro.build.version.release"),
        "android_sdk": _shell("getprop ro.build.version.sdk"),
    }
    _metadata(dest).write_text(json.dumps(meta, indent=2) + "\n")
    _metadata(dest).chmod(0o600)
    log(f"backed up the login to {dest} ({dest.stat().st_size // 1024} KiB, Instagram {version})")
    return dest


def _check_archive(path: Path) -> None:
    """LoginBackupError unless `path` is a tar of the app's directory holding its shared_prefs, the least
    a logged-in session leaves; every member stays under that directory."""
    names: list[str]
    tar: tarfile.TarFile
    try:
        with tarfile.open(path) as tar:
            names = tar.getnames()
    except (OSError, tarfile.TarError) as e:
        raise LoginBackupError(f"{path.name} isn't a readable tar: {e}") from None
    outside: list[str] = [n for n in names if n != config.IG_PKG and not n.startswith(f"{config.IG_PKG}/")]
    if outside or any(".." in Path(n).parts for n in names):
        raise LoginBackupError(f"{path.name} holds paths outside {config.IG_PKG}/: {outside[:3]}")
    if f"{config.IG_PKG}/shared_prefs" not in names:
        raise LoginBackupError(
            f"{path.name} has no {config.IG_PKG}/shared_prefs: not a copy of a used install"
        )


def restore_login(archive: Path | None = None, now: datetime | None = None) -> str:
    """Put a login backup (the newest, or `archive`) in place of the installed Instagram's app data.
    Returns where the data it replaced was moved on the device. Refuses a backup taken from a newer
    build than the installed one, whose databases the older build can't open."""
    found: list[Path] = backups()
    source: Path | None = archive or (found[-1] if found else None)
    if source is None:
        raise LoginBackupError(f"no login backup in {config.BACKUP_DIR}: run `scraper.py login-backup` first")
    if not source.is_file():
        raise LoginBackupError(f"no such file: {source}")
    taken_with: str | None = _backup_version(source)
    version: str = _prepare()
    if taken_with and version_key(taken_with) > version_key(version):
        raise LoginBackupError(
            f"{source.name} is from Instagram {taken_with}, newer than the installed {version}: install"
            f" {taken_with} or newer first (scraper.py install {taken_with})"
        )
    stamp: str = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    raw: Path = source.with_name(f"restore-{stamp}.tar.part")
    pushed: str = f"{DEVICE_TMP}/instadroid-login-{stamp}.tar"
    aside: str = f"{DEVICE_TMP}/instadroid-login-before-{stamp}"
    app_dir: str = f"{DATA_DIR}/{config.IG_PKG}"
    gz: gzip.GzipFile
    out: BufferedWriter
    try:
        with gzip.GzipFile(source, "rb") as gz, raw.open("wb") as out:
            raw.chmod(0o600)
            shutil.copyfileobj(gz, out)
        _check_archive(raw)
        _adb(["push", str(raw), pushed], timeout=COPY_TIMEOUT)
    except (OSError, EOFError, gzip.BadGzipFile) as e:
        raise LoginBackupError(f"{source.name} can't be read: {e}") from None
    finally:
        raw.unlink(missing_ok=True)
    owner: str = _shell(f"stat -c %u:%g {app_dir}")
    # Everything but what EXCLUDE leaves to Android moves aside, so a failed restore can be undone by hand.
    keep: str = " ".join(f"-e '^{name}$'" for name in EXCLUDE)
    _shell(
        f'mkdir -p {aside} && cd {app_dir} && for f in $(ls -A | grep -v {keep}); do mv "$f" {aside}/; done',
        timeout=COPY_TIMEOUT,
    )
    _shell(f"tar -xf {pushed} -C {DATA_DIR} && rm {pushed}", timeout=COPY_TIMEOUT)
    _shell(f"chown -R {owner} {app_dir} && restorecon -R {app_dir}", timeout=COPY_TIMEOUT)
    log(
        f"restored the login from {source.name} (taken with {taken_with or 'an unknown build'});"
        f" the replaced data is in {aside} on the device"
    )
    return aside


def _backup_version(archive: Path) -> str | None:
    """The Instagram build a backup's .json names, or None without one."""
    meta: JSON
    try:
        meta = loads(_metadata(archive).read_text())
    except OSError, ValueError:
        return None
    value: JSON = meta.get("instagram") if isinstance(meta, dict) else None
    return value if isinstance(value, str) else None
