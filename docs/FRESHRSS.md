# FreshRSS

Subscribe to `http://<host>:8000/instagram.xml` (set `PUBLIC_URL` in `.env` to whatever
FreshRSS can reach so image links resolve). Per-account feeds: `/instagram.xml?user=somebody`.
`/users` lists every account with a stored post. `/stories.xml` is a separate feed of captured
story frames, kept for `RETAIN_DAYS` like posts (see the README's "Stories") — subscribe to it separately if you want it. In each post,
@mentions and #hashtags in the caption link to that account's or hashtag's Instagram page.

**Filters**: `/instagram.xml` also takes `?kind=` (comma-separated `photo`, `carousel`, `reel`,
`post`; `video` is the same as `reel`), `?since=` (an ISO date or time; without an offset it's in
the `TZ` zone, compared with the posted time, else the saved one) and `?q=` (caption text,
case-insensitive for ASCII), alone or together and with `?user=`: `/instagram.xml?kind=reel&q=recipe`.
An unknown kind or an unreadable `since` is a 422.

**JSON Feed**: `/instagram.json` (the same parameters) and `/stories.json` serve the same entries as
[JSON Feed 1.1](https://www.jsonfeed.org/version/1.1/), for readers and scripts that prefer it.

**Feed auth**: the feed server is open to anything that can reach it, which is fine on the default
`FEED_HOST=127.0.0.1`. Before setting `FEED_HOST=0.0.0.0`, set `FEED_TOKEN` (or `FEED_TOKEN_FILE`):
every path except `/health` then needs the token, as `Authorization: Bearer <token>`, as the
password of HTTP basic auth (any username — FreshRSS's per-feed HTTP username/password fields), or
as `?token=<token>` on the feed URL. Images in the feeds keep loading in a reader without the token:
their URLs carry a signature that opens that one file only. `/opml` writes `?token=` into each feed
URL, so importing it (or a Dynamic OPML category, itself subscribed with `?token=`) needs no per-feed
setup. A `?token=` value is blanked in the access log. Browsers get a basic-auth prompt on `/status`.
Without a token, a non-loopback `FEED_HOST` logs a warning at startup.

If FreshRSS runs on the same host (see below), keep `PUBLIC_URL` on `http://127.0.0.1:8000` (the
default), not `http://localhost:8000`: a FreshRSS container's `localhost` can resolve to `::1`
first, and the feed server only binds the IPv4 loopback (`FEED_HOST=127.0.0.1` default), so
every subscription fails with "Failed to resolve domain".

**One-step bulk subscribe**: `/opml` is an OPML outline listing all of the above — the aggregate
feed, `/stories.xml`, and one entry per account in `/users` — nested under a single "Instagram"
category. It's a subscription list, not a feed: adding `/opml` as a feed URL fails with FreshRSS's
"A feed could not be found at …/opml" (SimplePie only parses RSS/Atom). Either:

- **Import it once**: Subscription management → Import/Export, then import the file saved from
  `/opml`.
- **Keep it in sync** (FreshRSS 1.20+): create a category and set its _Dynamic OPML_ URL to
  `$PUBLIC_URL/opml`. FreshRSS then re-reads the outline on its own schedule, so accounts that
  appear later (or a rename via `scraper.py rename`) are picked up without re-importing. It re-reads
  during a whole-account refresh only (the scraper's ping below, or its own cron), every 12 hours by
  default (_Dynamic OPML refresh interval_ in the user's settings); a feed that has left the outline
  is muted, not deleted, and unmuted when it returns, so an account whose stored posts all aged past
  `RETAIN_DAYS` goes quiet until it posts again. Before the scraper's first run there is no database,
  and `/opml` answers 503 rather than an outline without accounts, so a re-read then keeps the
  category as it was instead of muting every account feed.

The outline holds the aggregate feed and every per-account feed, and a reader dedupes entries within
a feed, not across feeds: subscribed to all of it, every post shows up twice. `/opml?aggregate=0`
leaves the aggregate feed out; to keep only the aggregate, subscribe to `/instagram.xml` and
`/stories.xml` directly.

With `OPML_FEED_TTL` set (seconds), each feed in the outline carries that as its FreshRSS refresh
interval (`frss:ttl`), so a ping fetches it however recently it was last fetched: FreshRSS skips a
feed whose interval hasn't passed, and its default is an hour. It applies when the OPML is imported
or re-read, not to feeds already subscribed; edit those in FreshRSS, or set the user's default
interval there instead.

**Push instead of poll**: by default a reader finds new posts on its own poll interval. To have
the scraper tell it instead, set `READER_REFRESH_URLS` in `.env` to the reader's refresh URL; after
any run that stores something new, the scraper requests each URL listed (whitespace-separated, so
one scraper can wake several readers), so the reader fetches immediately. A URL is a GET unless
it starts with its method, `PUT:` or `POST:`, and a `user:password@` in it is sent as HTTP basic
auth. A reader being unreachable is logged as a run warning (shows yellow on `/status`), never
fails the scrape.

- **FreshRSS**: its "online cron" actualize URL,
  `http://<freshrss-host>/i/?c=feed&a=actualize&user=<name>&token=<token>`, with the token of the
  user you create below. The scraper adds `ajax=1`, so FreshRSS answers without rendering a page, and
  `maxFeeds=1000`, since FreshRSS otherwise refreshes at most 10 feeds per request, the stalest
  first, and leaves the rest of an `/opml` import for the next ping; set either in the URL to override.
  A wrong user or token is a 403, reported as a run warning. FreshRSS answers once every due feed is
  fetched, which `READER_REFRESH_TIMEOUT` (60 s) allows for.
- **Miniflux**: its refresh-all API, `PUT:http://<user>:<password>@<miniflux-host>/v1/feeds/refresh`.
  Miniflux's API takes basic auth with the account's own login, and the call queues a refresh of
  every feed, the Instagram ones included.
- **Another reader**: any URL that a plain GET, PUT or POST makes refresh.

**Running FreshRSS on this host**: an optional `freshrss` service in `docker-compose.yml`, off by
default (`docker compose --profile freshrss up -d freshrss`). One-time setup after it's up:

```bash
docker compose exec freshrss ./cli/do-install.php \
  --default-user ivy --auth-type form --db-type sqlite
docker compose exec freshrss ./cli/create-user.php \
  --user ivy --password <a password> --token <a token> --no-default-feeds
# Required, not optional: do-install.php's own output tells you this, and skipping it fails every
# request (including the plain login page) with "Error during context user init!" — the install
# leaves data/users/<name> group-owned by root with no www-data access, so PHP running as www-data
# can't read the very config it just wrote.
docker compose exec --user root freshrss ./cli/access-permissions.sh
```

That token is what the FreshRSS URL in `READER_REFRESH_URLS` above is built from. FreshRSS listens on
`127.0.0.1:8080` by default (`FRESHRSS_LISTEN`) — loopback only, matching `FEED_HOST`, since the
feed itself has no auth unless `FEED_TOKEN` is set (see "Feed auth" above). It needs
`FRESHRSS_INTERNAL_HOST_ALLOWLIST` (defaulted in compose to `127.0.0.1:<FEED_PORT>`) to be allowed
to fetch a feed on `127.0.0.1` at all — FreshRSS 1.30+ blocks that as an SSRF guard otherwise. Its
own poller runs every 20 minutes by default (`FRESHRSS_CRON_MIN`), a fallback for when the ping is
unset or fails that costs a conditional GET per feed, answered 304 unless a run stored something;
set `FRESHRSS_CRON_MIN` empty to turn it off and refresh only on the scraper's ping.

## Troubleshooting

Symptoms as FreshRSS shows them (_Subscription management_, or `docker compose logs freshrss`),
and the setting behind each:

- **"Failed to resolve domain"** or **"Failed to connect"** on every feed: `PUBLIC_URL` names a host
  FreshRSS can't reach. From a container, `localhost` can resolve to `::1`, which the feed server
  doesn't bind (`FEED_HOST=127.0.0.1`); use `http://127.0.0.1:8000`. A FreshRSS without host
  networking has its own loopback, so it needs the host's LAN address, `FEED_HOST=0.0.0.0` and a
  `FEED_TOKEN`. The feed server warns at startup about a `PUBLIC_URL` that names `localhost` or has
  no scheme.
- **"Fetching this URL is not allowed, because the host's IP is not in the allowlist"** in the log, or
  feeds that fetch nothing with no error shown (FreshRSS 1.30+): the SSRF guard. The compose service
  sets `INTERNAL_HOST_ALLOWLIST` from `FEED_PORT`; a FreshRSS run any other way needs the same
  `host:port` entries under _System configuration_ or in its environment.
- **"Error getting dynamic OPML"**: the same two causes, for the `/opml` URL itself; with
  `FEED_TOKEN` set, the URL needs `?token=`. Before the scraper's first run it is expected: `/opml`
  answers 503 until the database exists.
- **Feeds subscribed but never refreshed**: nothing triggers a refresh. `FRESHRSS_CRON_MIN` was set
  empty (FreshRSS's own poller off) without `READER_REFRESH_URLS` (the scraper's ping), or the
  FreshRSS runs outside compose with no cron of its own. A ping refreshes only feeds whose interval
  has passed (`OPML_FEED_TTL` above), and the scraper logs each failed ping as a run warning on
  `/status`.
- **Only some accounts refresh per run**: a `READER_REFRESH_URLS` entry that sets its own `maxFeeds`
  below the account count.
- **Every post twice**: the aggregate feed beside the per-account ones; `/opml?aggregate=0`.
- **Images missing in entries**: image URLs are `PUBLIC_URL` plus `/media/…`, so the reader's browser
  must reach `PUBLIC_URL` too, not only the FreshRSS container.
- **The import rejects the saved file**: FreshRSS guesses a file's type from its name; keep `opml` in
  it (the server suggests `instadroid.opml`) or end it in `.xml`.
