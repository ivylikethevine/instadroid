"""_ping_freshrss(): the best-effort GET that tells FreshRSS to refresh after a run stores
something new. Fully offline — urllib.request.urlopen is monkeypatched, nothing hits the network.
FRESHRSS_REFRESH_URL/_TIMEOUT are read from config at call time, so tests monkeypatch config."""

import urllib.error
import urllib.request

import pytest
from instadroid import common, config, scrape

from tests.support import UrlResponse


def test_disabled_when_freshrss_refresh_url_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "FRESHRSS_REFRESH_URL", "")
    calls: list[str] = []

    def record(url: str, timeout: float | None = None) -> None:
        calls.append(url)

    monkeypatch.setattr(urllib.request, "urlopen", record)
    assert scrape._ping_freshrss(1, 0) is None
    assert calls == []


def test_no_op_when_nothing_new_was_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config, "FRESHRSS_REFRESH_URL", "http://127.0.0.1:8080/i/?c=feed&a=actualize&user=a&token=b"
    )
    calls: list[str] = []

    def record(url: str, timeout: float | None = None) -> None:
        calls.append(url)

    monkeypatch.setattr(urllib.request, "urlopen", record)
    assert scrape._ping_freshrss(0, 0) is None
    assert calls == []


def test_fires_on_new_posts_and_appends_ajax_param(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config, "FRESHRSS_REFRESH_URL", "http://127.0.0.1:8080/i/?c=feed&a=actualize&user=a&token=b"
    )
    monkeypatch.setattr(config, "FRESHRSS_REFRESH_TIMEOUT", 5.0)
    requested: list[tuple[str, float | None]] = []

    def fake_urlopen(url: str, timeout: float | None = None) -> UrlResponse:
        requested.append((url, timeout))
        return UrlResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert scrape._ping_freshrss(2, 0) is None
    [(url, timeout)] = requested
    assert "ajax=1" in url
    assert timeout == 5.0


def test_fires_on_new_stories_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config, "FRESHRSS_REFRESH_URL", "http://127.0.0.1:8080/i/?c=feed&a=actualize&user=a&token=b"
    )

    def fake_urlopen(url: str, timeout: float | None = None) -> UrlResponse:
        return UrlResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert scrape._ping_freshrss(0, 1) is None


def test_does_not_duplicate_an_already_present_ajax_param(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config,
        "FRESHRSS_REFRESH_URL",
        "http://127.0.0.1:8080/i/?c=feed&a=actualize&user=a&token=b&ajax=1",
    )
    requested: list[str] = []

    def fake_urlopen(url: str, timeout: float | None = None) -> UrlResponse:
        requested.append(url)
        return UrlResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    scrape._ping_freshrss(1, 0)
    [url] = requested
    assert url.count("ajax=") == 1


def test_connection_error_is_returned_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config, "FRESHRSS_REFRESH_URL", "http://127.0.0.1:8080/i/?c=feed&a=actualize&user=a&token=b"
    )

    def fake_urlopen(url: str, timeout: float | None = None) -> UrlResponse:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = scrape._ping_freshrss(1, 0)
    assert result is not None
    assert "connection refused" in result


def test_log_and_error_redact_the_query_string_so_the_token_never_prints(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        config,
        "FRESHRSS_REFRESH_URL",
        "http://127.0.0.1:8080/i/?c=feed&a=actualize&user=alice&token=SECRETTOKEN",
    )

    def fake_urlopen(url: str, timeout: float | None = None) -> UrlResponse:
        raise urllib.error.URLError("nope")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = scrape._ping_freshrss(1, 0)
    assert result is not None
    assert "SECRETTOKEN" not in result
    assert "SECRETTOKEN" not in capsys.readouterr().out
    assert "http://127.0.0.1:8080/i/" in result


def test_redact_url_drops_the_query_string() -> None:
    assert common.redact_url("http://127.0.0.1:8080/i/?a=1&token=secret") == "http://127.0.0.1:8080/i/"
