from pathlib import Path

import pytest
from instadroid import config
from shared.fileenv import env_secret


def test_plain_variable_when_no_file_is_set() -> None:
    assert env_secret("IG_PASSWORD", {"IG_PASSWORD": "hunter2"}) == "hunter2"
    assert env_secret("IG_PASSWORD", {}) == ""


def test_file_contents_win_with_one_trailing_newline_stripped(tmp_path: Path) -> None:
    secret: Path = tmp_path / "pw"
    secret.write_text("  hunter2 \n\n")  # only the last newline is `echo`'s; the rest is the value
    assert env_secret("IG_PASSWORD", {"IG_PASSWORD_FILE": str(secret)}) == "  hunter2 \n"
    secret.write_text("hunter2\r\n")
    assert env_secret("IG_PASSWORD", {"IG_PASSWORD_FILE": str(secret)}) == "hunter2"


def test_an_empty_file_variable_is_ignored() -> None:
    assert env_secret("IG_PASSWORD", {"IG_PASSWORD": "hunter2", "IG_PASSWORD_FILE": ""}) == "hunter2"


def test_both_set_is_an_error(tmp_path: Path) -> None:
    secret: Path = tmp_path / "pw"
    secret.write_text("hunter2")
    with pytest.raises(RuntimeError, match="both IG_PASSWORD and IG_PASSWORD_FILE"):
        env_secret("IG_PASSWORD", {"IG_PASSWORD": "x", "IG_PASSWORD_FILE": str(secret)})


def test_an_unreadable_file_is_an_error_not_an_empty_secret(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="IG_PASSWORD_FILE=.*can't be read"):
        env_secret("IG_PASSWORD", {"IG_PASSWORD_FILE": str(tmp_path / "missing")})


def test_a_choice_setting_falls_back_to_its_default_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("IG_TEST_CHOICE", " Chrono ")
    assert config._choice("IG_TEST_CHOICE", "following", ("following", "chrono")) == "chrono"
    monkeypatch.setenv("IG_TEST_CHOICE", "sideways")
    assert config._choice("IG_TEST_CHOICE", "following", ("following", "chrono")) == "following"
    assert "WARN: unknown IG_TEST_CHOICE 'sideways'; falling back to following" in capsys.readouterr().out
    monkeypatch.delenv("IG_TEST_CHOICE")
    assert config._choice("IG_TEST_CHOICE", "following", ("following", "chrono")) == "following"
