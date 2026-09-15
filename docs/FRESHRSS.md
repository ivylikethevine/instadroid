# FreshRSS

Subscribe to `http://<host>:8000/instagram.xml` (set `PUBLIC_URL` in compose to whatever
FreshRSS can reach so image links resolve). Per-account feeds: `/instagram.xml?user=somebody`.
`/users` lists everyone seen so far. `/stories.xml` is a separate feed of currently-unexpired
stories (see the README's "Stories") — subscribe to it separately if you want it. In each post,
@mentions and #hashtags in the caption link to that account's or hashtag's Instagram page.

**Feed auth**: the feed server is open to anything that can reach it, which is fine on the default
`FEED_HOST=127.0.0.1`. Before setting `FEED_HOST=0.0.0.0`, set `FEED_TOKEN` (or `FEED_TOKEN_FILE`):
every path except `/health` then needs the token, as `Authorization: Bearer <token>`, as the
password of HTTP basic auth (any username — FreshRSS's per-feed HTTP username/password fields), or
as `?token=<token>` on the feed URL. Images in the feeds keep loading in a reader without the token:
their URLs carry a signature that opens that one file only. `/opml` writes `?token=` into each feed
URL, so importing it (or a Dynamic OPML category, itself subscribed with `?token=`) needs no per-feed
setup. A `?token=` value is blanked in the access log. Browsers get a basic-auth prompt on `/status`.
Without a token, a non-loopback `FEED_HOST` logs a warning at startup.

If FreshRSS runs on the same host (see below), set `PUBLIC_URL=http://127.0.0.1:8000`, not
`http://localhost:8000` — confirmed the hard way: a FreshRSS container's `localhost` resolved to
`::1` first, and the feed server only binds the IPv4 loopback (`FEED_HOST=127.0.0.1` default), so
every subscription failed with "Failed to resolve domain" until `PUBLIC_URL` used the literal IP.

**One-step bulk subscribe**: `/opml` is an OPML outline listing all of the above — the aggregate
feed, `/stories.xml`, and one entry per account in `/users` — nested under a single "Instagram"
category. It's a subscription list, not a feed: adding `/opml` as a feed URL fails with FreshRSS's
"A feed could not be found at …/opml" (SimplePie only parses RSS/Atom). Either:

- **Import it once**: Subscription management → Import/Export, then import the file saved from
  `/opml`.
- **Keep it in sync** (FreshRSS 1.20+): create a category and set its _Dynamic OPML_ URL to
  `$PUBLIC_URL/opml`. FreshRSS then re-reads the outline on its own schedule, so accounts that
  appear later (or a rename via `scraper.py rename`) are picked up without re-importing.

**Push instead of poll**: by default FreshRSS finds new posts on its own poll interval. To have
the scraper tell it instead, set `FRESHRSS_REFRESH_URL` in `.env` to FreshRSS's "online cron"
actualize URL (`http://<freshrss-host>/i/?c=feed&a=actualize&user=<name>&token=<token>` — the
token comes from the user you create below); after any run that stores something new, the scraper
GETs that URL so FreshRSS fetches immediately. A reader being unreachable is logged as a run
warning (shows yellow on `/status`), never fails the scrape.

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

That token is what `FRESHRSS_REFRESH_URL` above is built from. FreshRSS listens on
`127.0.0.1:8080` by default (`FRESHRSS_LISTEN`) — loopback only, matching `FEED_HOST`, since the
feed itself has no auth unless `FEED_TOKEN` is set (see "Feed auth" above). It needs
`FRESHRSS_INTERNAL_HOST_ALLOWLIST` (defaulted in compose) to be allowed to fetch a feed on
`127.0.0.1` at all — FreshRSS 1.30+ blocks that as an SSRF guard otherwise.
