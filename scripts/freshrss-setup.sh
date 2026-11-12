#!/usr/bin/env bash
# One-time setup of the optional `freshrss` compose service (docs/FRESHRSS.md): starts it, installs
# FreshRSS, creates a user, and imports a category that follows this instance's /opml, so every
# account is subscribed and an account that appears later is picked up.
#
#   scripts/freshrss-setup.sh <user>   # asks for a password; FRESHRSS_PASSWORD answers it instead
#
# The app service has to be running: the feed URLs and FEED_TOKEN come from it. Safe to run again: an
# install, a user or a followed list that is already there is left as it is.
set -euo pipefail

user="${1:-}"
if [[ ! "$user" =~ ^[0-9A-Za-z]{1,38}$ ]]; then
  echo "usage: scripts/freshrss-setup.sh <user>   (letters and digits, 38 at most)" >&2
  exit 2
fi

cd "$(dirname "$0")/.."

dc() { docker compose --profile freshrss "$@"; }
frss() { dc exec -T freshrss "$@"; }

if ! dc ps --status running --services | grep -qx app; then
  echo "the app service is not running; start it first: docker compose up -d" >&2
  exit 1
fi

dc up -d freshrss
# The image's entrypoint prepares the data directory before it starts the web server.
for _ in $(seq 60); do
  if frss pgrep httpd >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if ! frss pgrep httpd >/dev/null; then
  echo "the freshrss service did not start; see: docker compose logs freshrss" >&2
  exit 1
fi

# The file do-install.php itself takes as "already installed".
if frss test -f data/applied_migrations.txt; then
  echo "FreshRSS is already installed"
else
  frss ./cli/do-install.php --default-user "$user" --auth-type form --db-type sqlite
fi

token=""
if frss ./cli/list-users.php | grep -qx "$user"; then
  echo "user $user already exists; its password and token are unchanged"
else
  password="${FRESHRSS_PASSWORD:-}"
  if [ -z "$password" ]; then
    read -rsp "Password for the FreshRSS user $user (7 characters or more): " password
    echo
  fi
  token="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
  frss ./cli/create-user.php --user "$user" --password "$password" --token "$token" --no-default-feeds
fi

# The category's Dynamic OPML URL, with FEED_TOKEN when there is one, shows up in the user's export.
if frss ./cli/export-opml-for-user.php --user "$user" 2>/dev/null | grep -q 'frss:opmlUrl="[^"]*/opml[?"]'; then
  echo "user $user already follows an /opml list"
else
  dc exec -T app python -m feedserver.freshrss | frss sh -c 'cat >/tmp/instadroid.opml'
  imported=0
  frss ./cli/import-for-user.php --user "$user" --filename /tmp/instadroid.opml || imported=$?
  frss rm -f /tmp/instadroid.opml
  if [ "$imported" -ne 0 ]; then
    echo "the import failed; add the list by hand instead, from the feed server's /subscribe page" >&2
    exit "$imported"
  fi
  # Reads the list and fetches its feeds now rather than on the next poll. Before the scraper's first
  # run /opml answers 503, and a later poll or ping picks the accounts up.
  frss ./cli/actualize-user.php --user "$user" ||
    echo "the first refresh failed; FreshRSS tries again on its next poll" >&2
fi

# Everything above wrote as root; the web server reads it as www-data.
dc exec -T --user root freshrss ./cli/access-permissions.sh

# LISTEN is `address:port` or a port alone; either way the scraper and a browser here use loopback.
listen="$(frss printenv LISTEN)"
listen="127.0.0.1:${listen##*:}"
echo
echo "FreshRSS is ready at http://$listen (user $user)."
echo "To have the scraper refresh it after each run, set this in .env and restart the app service:"
echo "  READER_REFRESH_URLS=http://$listen/i/?c=feed&a=actualize&user=$user&token=${token:-<the token the user was created with>}"
