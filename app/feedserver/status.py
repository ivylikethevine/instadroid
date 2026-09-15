"""Is the scraper healthy: /health for the compose healthcheck, /status for a person."""

import sqlite3
from datetime import UTC, datetime, timedelta
from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from shared.sqlrows import has_column

from . import control, queries, render, settings
from .queries import integer, string, text

router = APIRouter()


class Health(BaseModel):
    ok: bool
    posts: int
    reason: str | None = None


def scraper_health(now: datetime | None = None) -> tuple[str, str]:
    """("", "") when healthy, else (short label, reason). OVERDUE: no run has finished within
    POLL_MAX_HOURS + 30min of the last one, i.e. the loop itself looks stuck (a run still in
    progress fits comfortably inside that slack). FAILING: no *successful* run for two full poll
    cycles plus an hour, e.g. a login challenge nobody has answered yet. No runs at all is healthy —
    the scraper may simply be on its first one."""
    last_finished, last_ok, first_started = queries.run_times()
    now = now or datetime.now(UTC)
    finished = render.parse_dt(last_finished)
    if finished and now - finished > timedelta(hours=settings.POLL_MAX_HOURS + 0.5):
        hours = (now - finished).total_seconds() / 3600
        return "OVERDUE", f"no scrape run has finished in {hours:.1f}h"
    reference = render.parse_dt(last_ok) or render.parse_dt(first_started)
    if reference and now - reference > timedelta(hours=2 * settings.POLL_MAX_HOURS + 1):
        hours = (now - reference).total_seconds() / 3600
        return "FAILING", f"no successful scrape run in {hours:.1f}h"
    return "", ""


@router.get(
    "/health",
    response_model=Health,
    response_model_exclude_none=True,
    responses={503: {"model": Health, "description": "Scraping looks stuck or keeps failing."}},
    summary="Scraper health",
)
def health() -> Health | JSONResponse:
    """`ok` unless no run has finished for too long or none has succeeded for too long (then 503,
    with a `reason`). Never needs the feed token."""
    count = queries.post_count()
    label, reason = scraper_health()
    if label:
        # 503 so the compose healthcheck (which only checks for a 2xx) marks the container unhealthy.
        return JSONResponse({"ok": False, "posts": count, "reason": reason}, status_code=503)
    return Health(ok=True, posts=count)


# --- the status page's cells ------------------------------------------------------------------------


def short_error(error: str, limit: int = 140) -> str:
    """First line only, capped: some exceptions (e.g. a uiautomator2 server crash) embed a whole
    multi-KB Java stack trace in their own str(), which would otherwise blow up this table."""
    first_line = error.split("\n", 1)[0]
    return first_line[: limit - 1] + "…" if len(first_line) > limit else first_line


def _duration(run: sqlite3.Row) -> str:
    start, end = render.parse_dt(text(run, "started_at")), render.parse_dt(text(run, "finished_at"))
    if not start or not end:
        return "—"
    m, s = divmod(int((end - start).total_seconds()), 60)
    return f"{m}m {s}s" if m else f"{s}s"


def _link_failures(run: sqlite3.Row) -> str:
    """ "sheet/clipboard" failure counts for a run, or "—" for a row from before these columns."""
    if not has_column(run, "link_sheet_failures"):
        return "—"
    return f"{integer(run, 'link_sheet_failures') or 0} sheet / {integer(run, 'link_clipboard_failures') or 0} clipboard"


def _run_count(run: sqlite3.Row, col: str) -> str:
    """An integer runs column as text, or "—" for a row from before that column existed."""
    return str(integer(run, col) or 0) if has_column(run, col) else "—"


def _run_memory(run: sqlite3.Row) -> str:
    """redroid's peak memory during a run ("1843 MiB"), plus its OOM kills when there were any, or
    "—" when it wasn't measured (the cgroup wasn't readable, or a row from before these columns)."""
    peak, ooms = queries.col_int(run, "mem_peak_mb"), queries.col_int(run, "oom_kills")
    if peak is None:
        return "—"
    return f"{peak} MiB" + (f", {ooms} OOM kill(s)" if ooms else "")


def _run_text(run: sqlite3.Row, col: str) -> str:
    """A text column off a runs row, or "" when it's NULL or the row predates that column."""
    return queries.col_text(run, col) or ""


def _run_result(run: sqlite3.Row) -> tuple[str, str]:
    """(css class, text) for a run's Result cell: its error, else its warning, else "ok"."""
    if error := text(run, "error"):
        return "err", short_error(error)
    if warning := _run_text(run, "warning"):
        return "warn", f"warn: {short_error(warning)}"
    return "", "ok"


@router.get("/status", response_class=HTMLResponse, summary="Status page")
def status_page() -> HTMLResponse:
    """A plain-HTML page of recent runs, the device, and per-account totals."""
    users = queries.user_counts()
    total = sum(integer(u, "n") or 0 for u in users)
    stories_count = queries.stories_stats()[0]
    runs = queries.recent_runs()
    device = queries.latest_device()
    latest = runs[0] if runs else None
    health_label, health_reason = scraper_health()  # same verdict /health gives the healthcheck

    device_line = "no successful run yet"
    if device:
        device_line = f"Android {escape(text(device, 'android_release') or '?')}"
        if sdk := text(device, "android_sdk"):
            device_line += f" (API {escape(sdk)})"
        if product := text(device, "device_product"):
            device_line += f" — {escape(product)}"
        if ig := _run_text(device, "ig_version"):
            device_line += f" · Instagram {escape(ig)}"
        if profile := _run_text(device, "selector_profile"):
            device_line += f" (profile {escape(profile)})"
        if image := _run_text(device, "redroid_image"):
            device_line += f" · {escape(image)}"

    if not latest:
        latest_html = "<p>No scrape runs recorded yet.</p>"
    else:
        warning = _run_text(latest, "warning")
        error = text(latest, "error")
        new_posts = integer(latest, "new_posts")
        if error or health_label:
            status_word, status_class = ("ERROR" if error else health_label), "bad"
        elif warning:
            status_word, status_class = "WARN", "warn"
        else:
            status_word, status_class = "OK", "good"
        latest_html = f"""
        <p><span class="badge {status_class}">{status_word}</span>
           finished {escape(string(latest, "finished_at"))} ({_duration(latest)}),
           {new_posts if new_posts is not None else 0} new post(s)</p>
        {f'<p class="err">{escape(short_error(error))}</p>' if error else ""}
        {f'<p class="err">{escape(health_reason)}</p>' if health_reason else ""}
        {f'<p class="warn">{escape(warning)}</p>' if warning and not error else ""}
        """
    latest_html += "".join(
        f'<p class="err">Alert since {escape(string(a, "raised_at")[:16])}: {escape(string(a, "message"))}</p>'
        for a in queries.open_alerts()
    )
    state = control.current_state()
    if state.locked:
        latest_html += '<p class="warn">Manual lock in place: scheduled runs are held.</p>'
    if state.scrape_now:
        latest_html += "<p>A scrape-now request is waiting for the poll loop.</p>"

    def _result_cell(r: sqlite3.Row) -> str:
        css, cell_text = _run_result(r)
        return f'<td class="{css}">{escape(cell_text)}</td>'

    def _new_posts_cell(r: sqlite3.Row) -> str:
        new_posts = integer(r, "new_posts")
        return f"<td>{new_posts if new_posts is not None else '—'}</td>"

    runs_rows = "".join(
        f"<tr><td>{escape(string(r, 'started_at'))}</td><td>{_duration(r)}</td>"
        f"{_new_posts_cell(r)}"
        f"<td>{escape(_run_count(r, 'new_stories'))}</td>"
        f"<td>{escape(_run_count(r, 'filtered_posts'))}</td>"
        f"<td>{escape(_link_failures(r))}</td>"
        f"<td>{escape(_run_text(r, 'ig_version') or '—')}</td>"
        f"<td>{escape(_run_memory(r))}</td>"
        f"{_result_cell(r)}</tr>"
        for r in runs
    )
    users_rows = "".join(
        f"<tr><td>{escape(string(u, 'username'))}</td><td>{string(u, 'n')}</td>"
        f"<td>{escape(text(u, 'latest') or '—')}</td></tr>"
        for u in users
    )

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta http-equiv="refresh" content="60">
<title>Instadroid status</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem;
        background: #fafafa; color: #222; }}
h1 {{ font-size: 1.3rem; }} h2 {{ font-size: 1rem; margin-top: 2rem; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ text-align: left; padding: 0.25rem 0.6rem; border-bottom: 1px solid #ddd; font-size: 0.9rem; }}
.badge {{ display: inline-block; padding: 0.1rem 0.5rem; border-radius: 0.3rem; font-weight: 600; }}
.badge.good {{ background: #d7f5da; color: #146c2e; }}
.badge.bad {{ background: #f8d7da; color: #842029; }}
.badge.warn {{ background: #fff3cd; color: #664d03; }}
.err {{ color: #842029; }}
.warn {{ color: #664d03; }}
</style></head>
<body>
<h1>Instadroid status</h1>
<p>{device_line}</p>
<h2>Last scrape</h2>
{latest_html}
<h2>Recent runs</h2>
<table><tr><th>Started</th><th>Duration</th><th>New</th><th>New stories</th><th>Filtered</th><th>Link fails</th><th>Instagram</th><th>Peak mem</th><th>Result</th></tr>{runs_rows or '<tr><td colspan="9">none</td></tr>'}</table>
<h2>Totals</h2>
<p>{total} post(s) stored across {len(users)} account(s), {stories_count} stor{"y" if stories_count == 1 else "ies"} stored</p>
<table><tr><th>Account</th><th>Posts</th><th>Latest</th></tr>{users_rows or '<tr><td colspan="3">none</td></tr>'}</table>
</body></html>"""
    return HTMLResponse(html)
