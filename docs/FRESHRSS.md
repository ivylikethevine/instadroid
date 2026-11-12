# FreshRSS

Subscribing FreshRSS, or another reader, to the feeds this instance serves.

## Contents

- [Quick start](#quick-start)
- [Feeds](#feeds)
- [The account list](#the-account-list)
- [Push instead of poll](#push-instead-of-poll)
- [Running FreshRSS on this host](#running-freshrss-on-this-host)
- [Troubleshooting](#troubleshooting)

## Quick start

Open `http://<host>:8000/subscribe` in a browser (`/status` links to it). It lists the URLs below
ready to paste: written for the address you opened the page at, with the token when `FEED_TOKEN` is
set, and with the entry FreshRSS's allow list needs. Pick one of the two ways; with both, every post
shows up twice.

1. **Everything in one feed.** In FreshRSS: _Subscription management_ → _Add a feed or category_ →
   _Add a feed_, and paste `http://<host>:8000/instagram.xml`. Stories are a feed of their own,
   `/stories.xml`.
2. **One feed per account.** _Subscription management_ → _Add a feed or category_ → _Add dynamic
   OPML_: name the category and paste `http://<host>:8000/opml` as its _OPML URL_. FreshRSS then
   follows the list as accounts come and go. `/opml` is a list of feeds, not a feed: pasted under
   _Add a feed_ it fails with "The newsfeed could not be added".

Where FreshRSS runs decides what `<host>` is and what else it needs:

- **The `freshrss` service of this repository's compose file**: run `scripts/freshrss-setup.sh <user>`
  and skip the steps above; it installs FreshRSS and subscribes that user the second way
  ([Running FreshRSS on this host](#running-freshrss-on-this-host)).
- **Anywhere else** (another machine, or a container with its own network): the feed server listens on
  loopback only until `.env` sets `FEED_HOST=0.0.0.0`, which wants a `FEED_TOKEN` beside it. Set
  `PUBLIC_URL` to the address FreshRSS and your browser reach the server at
  (`http://192.168.1.20:8000`), restart the `app` service, and open `/subscribe` at that address.
  FreshRSS 1.30 and later also refuses a private address until its `host:port` is on its allow list
  (`INTERNAL_HOST_ALLOWLIST`, or _System configuration_).

When a step fails, [Troubleshooting](#troubleshooting) lists each message FreshRSS shows.

## Feeds

`/instagram.xml` is every account in one feed; `/instagram.xml?user=somebody` is one account, and
`/users` lists every account with a stored post. `/stories.xml` is a separate feed of captured
story frames, kept for `RETAIN_DAYS` like posts (see the README's "Stories"). In each post,
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
URL, so a Dynamic OPML category (itself subscribed with `?token=`) needs no per-feed setup, and
`/subscribe` shows every URL with it. A `?token=` value is blanked in the access log. Browsers get a
basic-auth prompt on `/status`, `/browse` and `/subscribe`. Without a token, a non-loopback
`FEED_HOST` logs a warning at startup.

**`PUBLIC_URL`** is what every image link in the feeds starts with, so it has to be an address the
browser you read in can load, not only one FreshRSS can fetch. Set, it is also what the feed URLs in
`/opml` and on `/subscribe` start with. Unset, images are linked from `http://127.0.0.1:8000`, and
those feed URLs start with the address the request came in on instead, so a list fetched at an
address subscribes to feeds at that same address. Behind a reverse proxy, set it to the proxy's URL.
Keep a loopback `PUBLIC_URL` on `http://127.0.0.1:8000`, not `http://localhost:8000`: a FreshRSS
container's `localhost` can resolve to `::1` first, and the feed server only binds the IPv4 loopback
(`FEED_HOST=127.0.0.1` default), so every subscription fails with "Failed to resolve domain".

## The account list

`/opml` is an OPML outline of `/stories.xml` and one feed per account in `/users`, nested under a
single "Instagram" category. Two ways to use it:

- **Keep it in sync** (FreshRSS 1.20+): the Dynamic OPML category of the quick start. FreshRSS
  re-reads the outline on its own schedule, so accounts that appear later (or a rename via
  `scraper.py rename`) are picked up without re-importing. It re-reads during a whole-account refresh
  only (the scraper's ping below, or its own cron), every 12 hours by default (_Dynamic OPML refresh
  interval_ in the user's settings); a feed that has left the outline is muted, not deleted, and
  unmuted when it returns, so an account whose stored posts all aged past `RETAIN_DAYS` goes quiet
  until it posts again. Before the scraper's first run there is no database, and `/opml` answers 503
  (with `Retry-After: 60`) rather than an outline without accounts, so a re-read then keeps the
  category as it was instead of muting every account feed.
- **Import the file**: save it from `/subscribe`'s download link, then in FreshRSS _Subscription
  management_ → _Import / export_. The outline's category names `/opml` as its Dynamic OPML URL
  (`frss:opmlUrl`), so FreshRSS makes the imported category a Dynamic OPML one and keeps it in sync
  the same way, unless a category called "Instagram" already exists. Any other reader subscribes to
  what the file lists, and needs another import for accounts that appear later.

The outline leaves the aggregate feed out, since a reader dedupes entries within a feed, not across
feeds: beside the per-account feeds it would show every post twice. `/opml?aggregate=1` adds it.

With `OPML_FEED_TTL` set (seconds), each feed in the outline carries that as its FreshRSS refresh
interval (`frss:ttl`), so a ping fetches it however recently it was last fetched: FreshRSS skips a
feed whose interval hasn't passed, and its default is an hour. It applies when the OPML is imported
or re-read, not to feeds already subscribed; edit those in FreshRSS, or set the user's default
interval there instead.

## Push instead of poll

By default a reader finds new posts on its own poll interval. To have the scraper tell it instead,
set `READER_REFRESH_URLS` in `.env` to the reader's refresh URL; after any run that stores something
new, the scraper requests each URL listed (whitespace-separated, so one scraper can wake several
readers), so the reader fetches immediately. A URL is a GET unless it starts with its method, `PUT:`
or `POST:`, and a `user:password@` in it is sent as HTTP basic auth. A reader being unreachable is
logged as a run warning (shows yellow on `/status`), never fails the scrape.

- **FreshRSS**: its "online cron" actualize URL,
  `http://<freshrss-host>/i/?c=feed&a=actualize&user=<name>&token=<token>`, with the token of the
  user you create below. The scraper adds `ajax=1`, so FreshRSS answers without rendering a page, and
  `maxFeeds=1000`, since FreshRSS otherwise refreshes at most 10 feeds per request, the stalest
  first, and leaves the rest of an `/opml` list for the next ping; set either in the URL to override.
  A wrong user or token is a 403, reported as a run warning. FreshRSS answers once every due feed is
  fetched, which `READER_REFRESH_TIMEOUT` (60 s) allows for.
- **Miniflux**: its refresh-all API, `PUT:http://<user>:<password>@<miniflux-host>/v1/feeds/refresh`.
  Miniflux's API takes basic auth with the account's own login, and the call queues a refresh of
  every feed, the Instagram ones included.
- **Another reader**: any URL that a plain GET, PUT or POST makes refresh.

## Running FreshRSS on this host

An optional `freshrss` service in `docker-compose.yml`, off by default. With the `app` service
running, one command sets it up:

```bash
scripts/freshrss-setup.sh <user>   # asks for that user's password
```

It starts the service, installs FreshRSS, creates the user, and imports a category that follows
`/opml` (the second way of the quick start), then prints the `READER_REFRESH_URLS` line for `.env`
with the token it generated. Run again, it leaves an install, a user and a followed list that are
already there as they are.

The same by hand, when the script doesn't fit:

```bash
docker compose --profile freshrss up -d freshrss
docker compose exec freshrss ./cli/do-install.php \
  --default-user <user> --auth-type form --db-type sqlite
docker compose exec freshrss ./cli/create-user.php \
  --user <user> --password <a password> --token <a token> --no-default-feeds
# Required, not optional: do-install.php's own output tells you this, and skipping it fails every
# request (including the plain login page) with "Error during context user init!" — the install
# leaves data/users/<name> group-owned by root with no www-data access, so PHP running as www-data
# can't read the very config it just wrote.
docker compose exec --user root freshrss ./cli/access-permissions.sh
```

Then subscribe as in the quick start. That token is what the FreshRSS URL in `READER_REFRESH_URLS`
above is built from.

FreshRSS listens on `127.0.0.1:8080` by default (`FRESHRSS_LISTEN`) — loopback only, matching
`FEED_HOST`, since the feed itself has no auth unless `FEED_TOKEN` is set (see "Feed auth" above). It
needs `FRESHRSS_INTERNAL_HOST_ALLOWLIST` (defaulted in compose to `127.0.0.1:<FEED_PORT>`) to be
allowed to fetch a feed on `127.0.0.1` at all — FreshRSS 1.30+ blocks that as an SSRF guard
otherwise; with `PUBLIC_URL` set to another address, add that `host:port` to it. Its own poller runs
every 20 minutes by default (`FRESHRSS_CRON_MIN`), a fallback for when the ping is unset or fails
that costs a conditional GET per feed, answered 304 unless a run stored something; set
`FRESHRSS_CRON_MIN` empty to turn it off and refresh only on the scraper's ping.

## Troubleshooting

Symptoms as FreshRSS shows them (_Subscription management_, or `docker compose logs freshrss`),
and the setting behind each. `/subscribe` warns about the first three before FreshRSS is involved.

- **"The newsfeed could not be added"** for `/opml`, with "A feed could not be found at …/opml" in the
  log: it is a list of feeds, added under _Add dynamic OPML_, not _Add a feed_.
- **"The newsfeed could not be added"** for a feed, or **"Failed to resolve domain"** or **"Failed to
  connect"** on every feed: the URL names a host FreshRSS can't reach. From a container, `localhost` can resolve to `::1`, which the
  feed server doesn't bind (`FEED_HOST=127.0.0.1`); use `http://127.0.0.1:8000`. A FreshRSS without
  host networking has its own loopback, so it needs the host's LAN address, `FEED_HOST=0.0.0.0` and a
  `FEED_TOKEN`. The feed server warns at startup about a `PUBLIC_URL` that names `localhost` or has
  no scheme.
- **"Fetching this URL is not allowed, because the host's IP is not in the allowlist"** in the log, or
  feeds that fetch nothing with no error shown (FreshRSS 1.30+): the SSRF guard, which covers private
  LAN addresses as well as loopback. The compose service sets `INTERNAL_HOST_ALLOWLIST` from
  `FEED_PORT`; a FreshRSS run any other way needs the `host:port` of the feed server under _System
  configuration_ or in its environment.
- **"Error getting dynamic OPML"**: FreshRSS got no outline from the `/opml` URL. The two causes
  above; or, with `FEED_TOKEN` set, a URL without `?token=` (a 401). Before the scraper's first run it
  is expected: `/opml` answers 503 until the database exists, and FreshRSS waits the 60 seconds the
  answer asks for before it tries that URL again ("will first retry after" in its log).
- **Feeds subscribed but never refreshed**: nothing triggers a refresh. `FRESHRSS_CRON_MIN` was set
  empty (FreshRSS's own poller off) without `READER_REFRESH_URLS` (the scraper's ping), or the
  FreshRSS runs outside compose with no cron of its own. A ping refreshes only feeds whose interval
  has passed (`OPML_FEED_TTL` above), and the scraper logs each failed ping as a run warning on
  `/status`.
- **Only some accounts refresh per run**: a `READER_REFRESH_URLS` entry that sets its own `maxFeeds`
  below the account count.
- **Every post twice**: the aggregate feed beside the per-account ones. Unsubscribe from one of the
  two; a Dynamic OPML URL with `?aggregate=1` lists both.
- **Images missing in entries**: image URLs are `PUBLIC_URL` plus `/media/…`, so the reader's browser
  must reach `PUBLIC_URL` too, not only the FreshRSS container.
- **The import rejects the saved file**: FreshRSS guesses a file's type from its name; keep `opml` in
  it (the server suggests `instadroid.opml`) or end it in `.xml`.
