"""Fetching Instagram with apkeep and installing it over adb."""

import subprocess
import zipfile
from pathlib import Path

import uiautomator2 as u2

from . import config, device, versioning
from .common import log
from .device import DeviceNotReady


def _apk_version(version: str | None = None) -> str:
    """The Instagram build to fetch: `version` if given, else IG_APK_VERSION, else the active
    profile's apk_version. "latest" (or an empty string) means whatever apkeep resolves as latest."""
    if version is None:
        version = config.IG_APK_VERSION or versioning.PROFILE.apk_version
    version = version.strip()
    return "" if version.lower() == "latest" else version


def _run_checked(cmd: list[str], what: str) -> None:
    """Run a host command for an install step; any failure or timeout becomes DeviceNotReady (so the
    retry ladder handles it) carrying the tail of its stderr."""
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=config.APK_FETCH_TIMEOUT)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        stderr = getattr(e, "stderr", "") or ""
        raise DeviceNotReady(f"{what} failed: {e!r}: {stderr[-2000:]}") from e


def _fetch_instagram_apk(version: str | None = None) -> list[Path]:
    """Return the APK(s) to install: the base APK first, then any config.*.apk split. Reuses a
    bundle already sitting in APK_CACHE_DIR (from a previous fetch, or dropped there by hand) so a
    reinstall after e.g. a /data/system reset (see CLAUDE.md) costs nothing over the network; only
    runs apkeep when the cache is empty.

    `version` is resolved by _apk_version(). A pinned version gets its own APK_CACHE_DIR/<version>/
    folder, so switching between versions (e.g. back to 445 to compare profiles) never silently
    reinstalls whichever bundle happened to be cached last. "latest" keeps the top-level layout.
    """
    version = _apk_version(version)
    cache_dir = config.APK_CACHE_DIR / version if version else config.APK_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    # xapk_dir holds nothing but one unpacked Instagram bundle, so every *.apk in it belongs to
    # this install (unlike cache_dir itself, which also holds the .xapk apkeep downloaded).
    xapk_dir = cache_dir / "xapk"
    cached = sorted(xapk_dir.glob("*.apk")) if xapk_dir.is_dir() else []
    if not cached:
        cached = sorted(cache_dir.glob(f"{config.IG_PKG}*.apk"))
    if not cached:
        xapks = sorted(cache_dir.glob(f"{config.IG_PKG}*.xapk"))
        if not xapks:
            spec = f"{config.IG_PKG}@{version}" if version else config.IG_PKG
            log(f"fetching {spec} via apkeep (apk-pure)")
            _run_checked(
                ["apkeep", "-a", spec, "-d", "apk-pure", str(cache_dir)], f"apkeep fetch of {config.IG_PKG}"
            )
            xapks = sorted(cache_dir.glob(f"{config.IG_PKG}*.xapk"))
        if xapks:
            # apkeep hands back a bundle (base + per-density/abi/language splits); unpack it once
            # and cache the extracted APKs so a later reinstall skips both the download and this.
            with zipfile.ZipFile(xapks[-1]) as zf:
                zf.extractall(xapk_dir)
            cached = sorted(xapk_dir.glob("*.apk"))
        else:
            cached = sorted(cache_dir.glob(f"{config.IG_PKG}*.apk"))
    if not cached:
        raise DeviceNotReady(f"apkeep reported success but no {config.IG_PKG} apk was found in {cache_dir}")
    # The base APK (no "config." prefix) has to be install-multiple's first argument; order among
    # the config.*.apk splits themselves doesn't matter to adb.
    base = [p for p in cached if not p.name.startswith("config.")]
    splits = [p for p in cached if p.name.startswith("config.")]
    if not base:
        raise DeviceNotReady(
            f"no base apk (only config.* splits) found in {xapk_dir or config.APK_CACHE_DIR}"
        )
    return base + splits


def install_instagram(d: u2.Device, version: str | None = None, downgrade: bool = False) -> None:
    """Fetch (or reuse a cached) Instagram bundle and adb-install it, same as the manual
    `apkeep` + `install-multiple` steps in README.md's First-time setup. Raises DeviceNotReady on
    any failure so the caller's retry ladder (device.is_transient()) handles it rather than aborting the
    whole run. `downgrade` adds `-r -d`, replacing an installed newer version in place (allowed
    because redroid is a userdebug build).
    """
    apks = _fetch_instagram_apk(version)
    flags = ["-r", "-d"] if downgrade else []
    cmd = [
        "adb",
        "-s",
        config.ADB_ADDR,
        "install-multiple" if len(apks) > 1 else "install",
        *flags,
        *map(str, apks),
    ]
    log(f"installing {config.IG_PKG} ({len(apks)} apk(s))")
    _run_checked(cmd, f"adb install of {config.IG_PKG}")
    installed = device.instagram_version(d)
    log(f"installed {config.IG_PKG}", installed or "(version unknown)")
    versioning.activate_profile(installed)  # device.connect_device() activated before this version existed


def install_instagram_version(d: u2.Device, version: str | None = None) -> str | None:
    """`scraper.py install [VERSION]`: put exactly `version` (default: the active profile's
    apk_version, see _apk_version(); "latest" = newest on APKPure) on the device, replacing whatever
    is installed, including a newer version. A no-op if that version is already installed. Returns
    the installed versionName.

    Instagram's saved login lives in /data and survives the replace, but an older build may not
    accept data written by a newer one, so a downgrade can still need a fresh login."""
    version = _apk_version(version)
    current = device.instagram_version(d)
    if version and current == version:
        log(f"{config.IG_PKG} {current} already installed")
        return current
    log(f"replacing {config.IG_PKG} {current or '(not installed)'} with {version or 'latest'}")
    install_instagram(d, version, downgrade=True)
    installed = device.instagram_version(d)
    if version and installed != version:
        raise DeviceNotReady(
            f"asked adb to install {config.IG_PKG} {version}, but the device reports {installed}"
        )
    return installed
