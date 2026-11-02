"""_ping_readers(): the best-effort requests that tell each reader to refresh after a run stores
something new. Fully offline — urllib.request.urlopen is monkeypatched, nothing hits the network.
READER_REFRESH_URLS/_TIMEOUT are read from config at call time, so tests monkeypatch config."""

import base64
import urllib.error
import urllib.request

import pytest
from instadroid import common, config, scrape

from tests.support import UrlopenCalls, record_urlopen

FRESHRSS: str = "http://127.0.0.1:8080/i/?c=feed&a=actualize&user=a&token=b"
MINIFLUX: str = "PUT:https://admin:p%40ss@miniflux.example/v1/feeds/refresh"


def test_disabled_when_no_refresh_url_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "READER_REFRESH_URLS", [])
    requested: list[urllib.request.Request] = record_urlopen(monkeypatch).requests
    assert scrape._ping_readers(1, 0) is None
    assert requested == []


def test_no_op_when_nothing_new_was_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "READER_REFRESH_URLS", [FRESHRSS])
    requested: list[urllib.request.Request] = record_urlopen(monkeypatch).requests
    assert scrape._ping_readers(0, 0) is None
    assert requested == []


def test_fires_on_new_posts_as_a_get_and_appends_ajax_param(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "READER_REFRESH_URLS", [FRESHRSS])
    monkeypatch.setattr(config, "READER_REFRESH_TIMEOUT", 5.0)
    calls: UrlopenCalls = record_urlopen(monkeypatch)
    assert scrape._ping_readers(2, 0) is None
    request: urllib.request.Request
    [request] = calls.requests
    assert request.get_method() == "GET"
    assert request.full_url == FRESHRSS + "&ajax=1"
    assert calls.timeouts == [5.0]


def test_fires_on_new_stories_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "READER_REFRESH_URLS", [FRESHRSS])
    requested: list[urllib.request.Request] = record_urlopen(monkeypatch).requests
    assert scrape._ping_readers(0, 1) is None
    assert len(requested) == 1


def test_does_not_duplicate_an_already_present_ajax_param(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "READER_REFRESH_URLS", [FRESHRSS + "&ajax=1"])
    requested: list[urllib.request.Request] = record_urlopen(monkeypatch).requests
    scrape._ping_readers(1, 0)
    request: urllib.request.Request
    [request] = requested
    assert request.full_url.count("ajax=") == 1


def test_a_method_prefix_and_url_credentials_become_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Miniflux's refresh-all endpoint is a PUT with basic auth; the credentials move out of the URL."""
    monkeypatch.setattr(config, "READER_REFRESH_URLS", [MINIFLUX])
    requested: list[urllib.request.Request] = record_urlopen(monkeypatch).requests
    assert scrape._ping_readers(1, 0) is None
    request: urllib.request.Request
    [request] = requested
    assert request.get_method() == "PUT"
    assert request.full_url == "https://miniflux.example/v1/feeds/refresh"
    assert request.get_header("Authorization") == "Basic " + base64.b64encode(b"admin:p@ss").decode()


def test_a_non_method_prefix_is_part_of_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "READER_REFRESH_URLS", ["https://reader.example/refresh"])
    requested: list[urllib.request.Request] = record_urlopen(monkeypatch).requests
    scrape._ping_readers(1, 0)
    request: urllib.request.Request
    [request] = requested
    assert request.get_method() == "GET"
    assert request.full_url == "https://reader.example/refresh"
    assert not request.has_header("Authorization")


def test_every_reader_is_pinged_and_each_failure_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "READER_REFRESH_URLS", [FRESHRSS, MINIFLUX, "http://other.example/hook"])

    def refuse(request: urllib.request.Request) -> urllib.error.URLError | None:
        down: bool = "8080" in request.full_url or "other" in request.full_url
        return urllib.error.URLError("connection refused") if down else None

    requested: list[urllib.request.Request] = record_urlopen(monkeypatch, fail=refuse).requests
    result: str | None = scrape._ping_readers(1, 0)
    assert len(requested) == 3
    assert result is not None
    assert "127.0.0.1:8080/i/ failed" in result and "other.example/hook failed" in result
    assert "miniflux" not in result


def test_log_and_error_redact_the_query_string_so_the_token_never_prints(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        config,
        "READER_REFRESH_URLS",
        ["http://127.0.0.1:8080/i/?c=feed&a=actualize&user=alice&token=SECRETTOKEN"],
    )
    record_urlopen(monkeypatch, fail=urllib.error.URLError("nope"))
    result: str | None = scrape._ping_readers(1, 0)
    assert result is not None
    assert "SECRETTOKEN" not in result
    assert "SECRETTOKEN" not in capsys.readouterr().out
    assert "http://127.0.0.1:8080/i/" in result


def test_a_delivered_ping_logs_no_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(config, "READER_REFRESH_URLS", [MINIFLUX])
    record_urlopen(monkeypatch)
    scrape._ping_readers(1, 0)
    out: str = capsys.readouterr().out
    assert "pinged reader refresh (PUT https://miniflux.example/v1/feeds/refresh)" in out
    assert "p%40ss" not in out and "admin" not in out


def test_redact_url_drops_the_query_string() -> None:
    assert common.redact_url("http://127.0.0.1:8080/i/?a=1&token=secret") == "http://127.0.0.1:8080/i/"


def test_an_ipv6_host_keeps_its_brackets() -> None:
    url: str = "http://user:secret@[::1]:8080/hook?x=1"
    assert common.redact_url(url) == "http://[::1]:8080/hook"
    assert common.web_request(url).full_url == "http://[::1]:8080/hook?x=1"


def test_web_request_keeps_a_port_and_a_password_less_user() -> None:
    request: urllib.request.Request = common.web_request("http://token@reader.example:8443/hook?x=1")
    assert request.full_url == "http://reader.example:8443/hook?x=1"
    assert request.get_header("Authorization") == "Basic " + base64.b64encode(b"token:").decode()
