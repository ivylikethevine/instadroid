"""The login backup and restore, against a fake adb: what goes over the wire and what lands on disk."""

import gzip
import io
import json
import stat
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

import pytest
from instadroid import config, control, loginbackup

NOW: datetime = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
PKG: str = config.IG_PKG


def _tar(*names: str) -> bytes:
    """A tar holding each name as a small file (a name ending in / as a directory)."""
    buf: io.BytesIO = io.BytesIO()
    tar: tarfile.TarFile
    with tarfile.open(fileobj=buf, mode="w") as tar:
        name: str
        for name in names:
            info: tarfile.TarInfo = tarfile.TarInfo(name.rstrip("/"))
            if name.endswith("/"):
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                data: bytes = b"x"
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


APP_TAR: bytes = _tar(
    f"{PKG}/", f"{PKG}/shared_prefs/", f"{PKG}/shared_prefs/session.xml", f"{PKG}/databases/a.db"
)


class FakeAdb:
    """The adb CLI against a device with Instagram `version` installed (None: not installed)."""

    def __init__(self, version: str | None = "450.0.0.1.1", archive: bytes = APP_TAR, uid: str = "0") -> None:
        self.version: str | None = version
        self.archive: bytes = archive
        self.uid: str = uid
        self.calls: list[list[str]] = []
        self.pushed: dict[str, bytes] = {}

    def __call__(self, args: list[str], *, out: IO[bytes] | None = None, timeout: float = 60.0) -> str:
        self.calls.append(args)
        if args[0] == "exec-out" and out is not None:
            out.write(self.archive)
            return ""
        if args[0] == "push":
            self.pushed[args[2]] = Path(args[1]).read_bytes()
            return ""
        if args[0] != "shell":
            return ""
        command: str = args[1]
        answers: dict[str, str] = {
            "id -u": self.uid,
            f"dumpsys package {PKG}": f"  versionName={self.version}\n" if self.version else "",
            "getprop ro.build.version.release": "13",
            "getprop ro.build.version.sdk": "33",
            f"stat -c %u:%g /data/data/{PKG}": "10123:10123",
        }
        return answers.get(command, "")

    def shells(self) -> list[str]:
        return [c[1] for c in self.calls if c[0] == "shell"]


def _no_sleep(_seconds: float) -> None:
    pass


@pytest.fixture
def adb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeAdb:
    """A fake adb, the manual lock held, and backups under tmp_path."""
    fake: FakeAdb = FakeAdb()
    monkeypatch.setattr(loginbackup, "_adb", fake)
    monkeypatch.setattr(loginbackup, "_connect", lambda: None)
    monkeypatch.setattr(loginbackup.time, "sleep", _no_sleep)
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "backups"))
    control.set_lock(True)
    return fake


def test_backup_writes_an_owner_only_archive_and_what_it_came_from(adb: FakeAdb) -> None:
    path: Path = loginbackup.backup_login(now=NOW)
    assert path.name == "login-20261120T120000Z.tar.gz"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600  # a logged-in session
    gz: gzip.GzipFile
    with gzip.GzipFile(path, "rb") as gz:
        assert gz.read() == APP_TAR
    meta: Path = path.with_name("login-20261120T120000Z.json")
    assert json.loads(meta.read_text()) == {
        "instagram": "450.0.0.1.1",
        "android_release": "13",
        "android_sdk": "33",
    }
    assert stat.S_IMODE(meta.stat().st_mode) == 0o600
    assert not list(path.parent.glob("*.part"))
    # As root, with Instagram stopped, leaving out what Android and the APK rebuild.
    assert adb.calls[0] == ["root"]
    assert f"am force-stop {PKG}" in adb.shells()
    copy: list[str] = next(c for c in adb.calls if c[0] == "exec-out")
    assert copy[1] == (
        f"tar -cf - -C /data/data --exclude={PKG}/cache --exclude={PKG}/code_cache --exclude={PKG}/lib {PKG}"
    )
    assert loginbackup.backups() == [path]


def test_both_directions_refuse_without_the_manual_lock(adb: FakeAdb) -> None:
    control.set_lock(False)
    with pytest.raises(loginbackup.LoginBackupError, match="scraper.py lock"):
        loginbackup.backup_login(now=NOW)
    with pytest.raises(loginbackup.LoginBackupError, match="no login backup"):
        loginbackup.restore_login()
    assert adb.calls == []


def test_backup_refuses_without_instagram_or_root(adb: FakeAdb) -> None:
    adb.version = None
    with pytest.raises(loginbackup.LoginBackupError, match="isn't installed"):
        loginbackup.backup_login(now=NOW)
    adb.version, adb.uid = "450.0.0.1.1", "2000"
    with pytest.raises(loginbackup.LoginBackupError, match="uid 2000"):
        loginbackup.backup_login(now=NOW)
    assert not any(c[0] == "exec-out" for c in adb.calls)


@pytest.mark.parametrize(
    ("archive", "problem"),
    [
        (b"not a tar", "readable tar"),
        (_tar(f"{PKG}/databases/a.db"), "no com.instagram.android/shared_prefs"),
        (_tar(f"{PKG}/shared_prefs/", "etc/passwd"), "outside"),
        (_tar(f"{PKG}/shared_prefs/", f"{PKG}/../x"), "outside"),
    ],
)
def test_backup_refuses_an_archive_that_isn_t_the_app_s_data(
    adb: FakeAdb, archive: bytes, problem: str
) -> None:
    adb.archive = archive
    with pytest.raises(loginbackup.LoginBackupError, match=problem):
        loginbackup.backup_login(now=NOW)
    assert not list(Path(config.BACKUP_DIR).iterdir())  # nothing half-written left behind


def test_restore_moves_the_current_data_aside_then_extracts_chowns_and_relabels(adb: FakeAdb) -> None:
    backup: Path = loginbackup.backup_login(now=NOW)
    adb.calls.clear()
    aside: str = loginbackup.restore_login(now=NOW)
    assert aside == "/data/local/tmp/instadroid-login-before-20261120T120000Z"
    pushed: str = "/data/local/tmp/instadroid-login-20261120T120000Z.tar"
    assert adb.pushed == {pushed: APP_TAR}
    shells: list[str] = adb.shells()
    move: str = next(s for s in shells if s.startswith("mkdir"))
    assert aside in move and "grep -v -e '^cache$' -e '^code_cache$' -e '^lib$'" in move and "rm " not in move
    extract: int = shells.index(f"tar -xf {pushed} -C /data/data && rm {pushed}")
    assert shells.index(move) < extract
    assert shells[extract + 1] == f"chown -R 10123:10123 /data/data/{PKG} && restorecon -R /data/data/{PKG}"
    assert not list(backup.parent.glob("*.part"))


def test_restore_takes_a_named_file_and_refuses_a_newer_build_s_backup(adb: FakeAdb, tmp_path: Path) -> None:
    backup: Path = loginbackup.backup_login(now=NOW)
    adb.version = "449.0.0.1.1"
    with pytest.raises(loginbackup.LoginBackupError, match=r"newer than the installed 449\.0\.0\.1\.1"):
        loginbackup.restore_login(backup)
    assert not adb.pushed
    adb.version = "451.0.0.1.1"  # a newer install opens an older build's databases
    loginbackup.restore_login(backup, now=NOW)
    assert adb.pushed
    with pytest.raises(loginbackup.LoginBackupError, match="no such file"):
        loginbackup.restore_login(tmp_path / "missing.tar.gz")


def test_restore_of_a_corrupt_backup_changes_nothing_on_the_device(adb: FakeAdb) -> None:
    broken: Path = Path(config.BACKUP_DIR) / "login-20261120T120000Z.tar.gz"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"not gzip")
    with pytest.raises(loginbackup.LoginBackupError, match="can't be read"):
        loginbackup.restore_login()
    assert not adb.pushed
    assert not any(s.startswith(("mkdir", "tar", "chown")) for s in adb.shells())
    assert sorted(p.name for p in broken.parent.iterdir()) == [broken.name]


def test_root_is_retried_while_adbd_restarts(adb: FakeAdb, monkeypatch: pytest.MonkeyPatch) -> None:
    answers: list[str] = ["2000", "2000", "0"]
    real: FakeAdb = adb

    def restarting(args: list[str], *, out: IO[bytes] | None = None, timeout: float = 60.0) -> str:
        if args == ["shell", "id -u"]:
            return answers.pop(0)
        return real(args, out=out, timeout=timeout)

    monkeypatch.setattr(loginbackup, "_adb", restarting)
    assert loginbackup.backup_login(now=NOW).exists()
    assert answers == []
