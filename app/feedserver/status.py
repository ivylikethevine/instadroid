"""Is the scraper healthy: /health for the compose healthcheck, /status for a person."""

import sqlite3
from datetime import UTC, datetime, timedelta
from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from shared.errors import short_error
from shared.sqlrows import cell_int, has_column, opt_int, opt_str
from shared.timestamps import parse_iso

from . import control, queries, settings
from .queries import string, text

router = APIRouter()


class Health(BaseModel):
    ok: bool
    posts: int
    reason: str | None = None


def scraper_health(con: sqlite3.Connection, now: datetime | None = None) -> tuple[str, str]:
    """("", "") when healthy, else (short label, reason). OVERDUE: no run has finished within
    POLL_MAX_HOURS + 30min of the last one, i.e. the loop itself looks stuck (a run still in
    progress fits comfortably inside that slack). FAILING: no *successful* run for two full poll
    cycles plus an hour, e.g. a login challenge nobody has answered yet. No runs at all is healthy —
    the scraper may simply be on its first one."""
    last_finished, last_ok, first_started = queries.run_times(con)
    now = now or datetime.now(UTC)
    finished = parse_iso(last_finished)
    if finished and now - finished > timedelta(hours=settings.POLL_MAX_HOURS + 0.5):
        hours = (now - finished).total_seconds() / 3600
        return "OVERDUE", f"no scrape run has finished in {hours:.1f}h"
    reference = parse_iso(last_ok) or parse_iso(first_started)
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
    with queries.connection() as con:
        count = queries.post_count(con)
        label, reason = scraper_health(con)
    if label:
        # 503 so the compose healthcheck (which only checks for a 2xx) marks the container unhealthy.
        return JSONResponse({"ok": False, "posts": count, "reason": reason}, status_code=503)
    return Health(ok=True, posts=count)


# --- the status page's cells ------------------------------------------------------------------------


def _duration(run: sqlite3.Row) -> str:
    start, end = parse_iso(text(run, "started_at")), parse_iso(text(run, "finished_at"))
    if not start or not end:
        return "—"
    m, s = divmod(int((end - start).total_seconds()), 60)
    return f"{m}m {s}s" if m else f"{s}s"


def _link_failures(run: sqlite3.Row) -> str:
    """ "sheet/clipboard" failure counts for a run, or "—" for a row from before these columns."""
    if not has_column(run, "link_sheet_failures"):
        return "—"
    return f"{cell_int(run, 'link_sheet_failures') or 0} sheet / {cell_int(run, 'link_clipboard_failures') or 0} clipboard"


def _run_count(run: sqlite3.Row, col: str) -> str:
    """An integer runs column as text, or "—" for a row from before that column existed."""
    return str(cell_int(run, col) or 0) if has_column(run, col) else "—"


def _run_memory(run: sqlite3.Row) -> str:
    """redroid's peak memory during a run ("1843 MiB"), plus its OOM kills when there were any, or
    "—" when it wasn't measured (the cgroup wasn't readable, or a row from before these columns)."""
    peak, ooms = opt_int(run, "mem_peak_mb"), opt_int(run, "oom_kills")
    if peak is None:
        return "—"
    return f"{peak} MiB" + (f", {ooms} OOM kill(s)" if ooms else "")


def _run_result(run: sqlite3.Row) -> tuple[str, str]:
    """(css class, text) for a run's Result cell: its error, else its warning, else "ok"."""
    if error := text(run, "error"):
        return "err", short_error(error)
    if warning := opt_str(run, "warning"):
        return "warn", f"warn: {short_error(warning)}"
    return "", "ok"


@router.get("/status", response_class=HTMLResponse, summary="Status page")
def status_page() -> HTMLResponse:
    """A plain-HTML page of recent runs, the device, and per-account totals."""
    with queries.connection() as con:
        users = queries.user_counts(con)
        stories_count = queries.stories_stats(con)[0]
        runs = queries.recent_runs(con)
        device = queries.latest_device(con)
        alerts = queries.open_alerts(con)
        health_label, health_reason = scraper_health(con)  # same verdict /health gives the healthcheck
    total = sum(cell_int(u, "n") or 0 for u in users)
    latest = runs[0] if runs else None

    device_line = "no successful run yet"
    if device:
        device_line = f"Android {escape(text(device, 'android_release') or '?')}"
        if sdk := text(device, "android_sdk"):
            device_line += f" (API {escape(sdk)})"
        if product := text(device, "device_product"):
            device_line += f" — {escape(product)}"
        if ig := opt_str(device, "ig_version"):
            device_line += f" · Instagram {escape(ig)}"
        if profile := opt_str(device, "selector_profile"):
            device_line += f" (profile {escape(profile)})"
        if image := opt_str(device, "redroid_image"):
            device_line += f" · {escape(image)}"

    if not latest:
        latest_html = "<p>No scrape runs recorded yet.</p>"
    else:
        warning = opt_str(latest, "warning")
        error = text(latest, "error")
        new_posts = cell_int(latest, "new_posts")
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
        for a in alerts
    )
    state = control.current_state()
    if state.hold is not None:
        latest_html += f'<p class="err">Held until unlocked, Instagram needs a person: {escape(short_error(state.hold))}</p>'
    elif state.locked:
        latest_html += '<p class="warn">Manual lock in place: scheduled runs are held.</p>'
    if state.scrape_now:
        latest_html += "<p>A scrape-now request is waiting for the poll loop.</p>"

    def _result_cell(r: sqlite3.Row) -> str:
        css, cell_text = _run_result(r)
        return f'<td class="{css}">{escape(cell_text)}</td>'

    def _new_posts_cell(r: sqlite3.Row) -> str:
        new_posts = cell_int(r, "new_posts")
        return f"<td>{new_posts if new_posts is not None else '—'}</td>"

    runs_rows = "".join(
        f"<tr><td>{escape(string(r, 'started_at'))}</td><td>{_duration(r)}</td>"
        f"{_new_posts_cell(r)}"
        f"<td>{escape(_run_count(r, 'new_stories'))}</td>"
        f"<td>{escape(_run_count(r, 'filtered_posts'))}</td>"
        f"<td>{escape(_link_failures(r))}</td>"
        f"<td>{escape(opt_str(r, 'ig_version') or '—')}</td>"
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
