#!/usr/bin/env bash
# Check a host before redroid's first start on it. Read-only: it starts no container, loads no module
# and changes nothing. Each check prints one line, OK, WARN or FAIL, and the script exits 1 when any
# check fails.
#
#   scripts/preflight.sh
#
# The checks: a supported CPU architecture; Docker and compose v2, not rootless; exactly one binder
# driver (a built-in one and the out-of-tree binder_linux together panicked a host,
# docs/INCIDENTS.md); memory, CPUs and disk for the limits in .env; the ADB and feed ports free;
# `.env` in place with real credentials; the /data volume's Android version matching the configured
# image; and whether scripts/diagnose.sh can read the kernel log. Settings are read from .env (only
# REDROID_MEM_LIMIT, REDROID_CPUS and FEED_PORT; nothing in it is printed), else their defaults.
set -euo pipefail

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  sed -n '2,13s/^# \{0,1\}//p' "$0"
  exit 0
fi

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

failures=0
ok() { echo "OK    $*"; }
warn() { echo "WARN  $*"; }
fail() {
  echo "FAIL  $*"
  failures=$((failures + 1))
}

# A value set in .env (the last uncommented line for the key, quotes stripped), else the default.
setting() {
  local value=""
  if [ -r .env ]; then
    value="$(sed -n "s/^[[:space:]]*$1=//p" .env | tail -n 1 | tr -d "\"'" | tr -d '[:space:]')"
  fi
  printf '%s\n' "${value:-$2}"
}

# A Docker memory size (3g, 3072m, 512M, a plain byte count) in MiB; empty when unreadable.
mib() {
  printf '%s\n' "$1" | tr '[:upper:]' '[:lower:]' | sed -n \
    -e 's/^\([0-9][0-9]*\)gb\{0,1\}$/\1 1024/p' \
    -e 's/^\([0-9][0-9]*\)mb\{0,1\}$/\1 1/p' \
    -e 's/^\([0-9][0-9]*\)b\{0,1\}$/\1 0/p' | awk '{ print ($2 ? $1 * $2 : int($1 / 1048576)) }'
}

# --- architecture ---------------------------------------------------------------------------------
arch="$(uname -m)"
case "$arch" in
x86_64) ok "architecture $arch" ;;
aarch64 | arm64) warn "architecture $arch: not supported yet; the compose file's redroid image is x86_64 (docs/ROADMAP.md, arm64 host support)" ;;
*) fail "architecture $arch: redroid's images are x86_64 and arm64 only" ;;
esac

# --- Docker ---------------------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  fail "docker not found"
elif ! security="$(docker info --format '{{json .SecurityOptions}}' 2>/dev/null)"; then
  fail "the Docker daemon isn't reachable (is it running, and is this user allowed to use it?)"
else
  ok "Docker daemon reachable"
  case "$security" in
  *rootless*) fail "Docker runs rootless: redroid needs a privileged container with the host's binder" ;;
  *) ok "Docker isn't rootless (privileged containers possible)" ;;
  esac
  if compose="$(docker compose version --short 2>/dev/null)"; then
    ok "docker compose $compose"
  else
    fail "docker compose (v2) not found"
  fi
fi

# --- binder ---------------------------------------------------------------------------------------
kconfig=""
if [ -r /proc/config.gz ] && command -v zcat >/dev/null 2>&1; then
  kconfig="$(zcat /proc/config.gz 2>/dev/null || true)"
elif [ -r "/boot/config-$(uname -r)" ]; then
  kconfig="$(cat "/boot/config-$(uname -r)")"
fi
# Here-strings, not pipes: under pipefail a grep -q that stops early fails the writer with SIGPIPE.
builtin=""
if grep -q '^CONFIG_ANDROID_BINDER_IPC_RUST=y' <<<"$kconfig"; then
  builtin="Rust binder (CONFIG_ANDROID_BINDER_IPC_RUST=y)"
elif grep -q '^CONFIG_ANDROID_BINDER_IPC=y' <<<"$kconfig"; then
  builtin="binder (CONFIG_ANDROID_BINDER_IPC=y)"
fi
module=""
if [ -d /sys/module/binder_linux ]; then
  module="binder_linux (loaded)"
elif command -v modinfo >/dev/null 2>&1 && modinfo -n binder_linux >/dev/null 2>&1; then
  module="binder_linux (installed, e.g. binder_linux-dkms)"
fi
if [ -n "$builtin" ] && [ -n "$module" ]; then
  fail "two binder drivers: the kernel's built-in $builtin and $module. Together they panicked a host" \
    "(docs/INCIDENTS.md); remove the module (e.g. uninstall binder_linux-dkms) and reboot"
elif [ -n "$builtin" ]; then
  ok "one binder driver: the kernel's built-in $builtin"
elif [ -n "$module" ]; then
  ok "one binder driver: $module"
elif [ -z "$kconfig" ] && grep -qw binder /proc/filesystems; then
  warn "binderfs is available, but without the kernel config (/proc/config.gz or /boot/config-$(uname -r))" \
    "this can't tell how many binder drivers there are; make sure there is exactly one"
elif [ -z "$kconfig" ]; then
  warn "can't read the kernel config (/proc/config.gz or /boot/config-$(uname -r)) to tell which binder" \
    "driver this kernel has; make sure there is exactly one"
else
  fail "no binder driver: redroid can't boot without one (a kernel with CONFIG_ANDROID_BINDER_IPC=y," \
    "or the binder_linux module, never both)"
fi

# --- memory, CPUs, disk ---------------------------------------------------------------------------
redroid_mem="$(setting REDROID_MEM_LIMIT 3g)"
redroid_mib="$(mib "$redroid_mem")"
if [ -z "$redroid_mib" ]; then
  warn "REDROID_MEM_LIMIT=$redroid_mem isn't a size this script reads; skipping the memory check"
else
  # redroid's limit, the app's 256m, and 512MiB for the host itself
  need=$((redroid_mib + 256 + 512))
  total=$(awk '/^MemTotal:/ { print int($2 / 1024) }' /proc/meminfo)
  available=$(awk '/^MemAvailable:/ { print int($2 / 1024) }' /proc/meminfo)
  if [ "$total" -lt "$need" ]; then
    fail "memory: ${total}MiB in total, under the ${need}MiB redroid ($redroid_mem), the app and the host need"
  elif [ "$available" -lt "$need" ]; then
    warn "memory: ${available}MiB available now, under the ${need}MiB redroid ($redroid_mem), the app and the host need"
  else
    ok "memory: ${available}MiB available, ${need}MiB needed"
  fi
fi
redroid_cpus="$(setting REDROID_CPUS 4)"
cpus="$(nproc)"
if awk -v have="$cpus" -v want="$redroid_cpus" 'BEGIN { exit !(have >= want + 2) }'; then
  ok "CPUs: $cpus, for redroid's $redroid_cpus and the app's 1.5"
else
  warn "CPUs: $cpus, little room beside redroid's $redroid_cpus (REDROID_CPUS) and the app's 1.5"
fi
free_gib="$(df -Pk . | awk 'NR == 2 { print int($4 / 1048576) }')"
if [ "$free_gib" -ge 10 ]; then
  ok "disk: ${free_gib}GiB free for local/"
else
  warn "disk: ${free_gib}GiB free for local/; Android's /data, the APK cache and snapshots want 10GiB or more"
fi

# --- ports ----------------------------------------------------------------------------------------
feed_port="$(setting FEED_PORT 8000)"
if [ "$(docker inspect -f '{{.State.Running}}' ig-redroid 2>/dev/null || true)" = "true" ]; then
  ok "ports: the stack is already running; skipping"
elif command -v ss >/dev/null 2>&1; then
  for port in 5555 "$feed_port"; do
    if [ -n "$(ss -ltnH "sport = :$port" 2>/dev/null)" ]; then
      fail "port $port is already in use (5555: ADB, $feed_port: the feed, FEED_PORT)"
    else
      ok "port $port free"
    fi
  done
else
  warn "ss not found; can't check that ports 5555 and $feed_port are free"
fi
if command -v adb >/dev/null 2>&1; then
  ok "adb on the host"
else
  warn "adb not found on the host: optional, but scrcpy and manual checks need it"
fi

# --- .env and the /data volume --------------------------------------------------------------------
if [ ! -e .env ]; then
  warn ".env not found: cp .env.example .env, then fill in the credentials"
elif grep -Eq '^[[:space:]]*IG_(USERNAME=username|PASSWORD=password)[[:space:]]*$' .env; then
  warn ".env still has the example credentials (IG_USERNAME/IG_PASSWORD, or their _FILE variants)"
else
  ok ".env in place"
fi
android_major() {
  printf '%s\n' "${1##*:}" | sed -n 's/^[^0-9]*\([0-9][0-9]*\).*/\1/p'
}
image="$(sed -n 's/^x-redroid-image: &redroid_image //p' docker-compose.yml)"
marker=local/data/android.image
if [ -s "$marker" ]; then
  recorded="$(head -n 1 "$marker")"
  if [ "$(android_major "$recorded")" != "$(android_major "$image")" ]; then
    fail "local/data/android was last used by $recorded, a different Android version from $image;" \
      "init will refuse to start redroid (docs/COMPATIBILITY.md, one Android version per /data volume)"
  else
    ok "/data volume last used by $recorded"
  fi
else
  ok "/data volume: none yet (init creates it)"
fi

# --- kernel log -----------------------------------------------------------------------------------
if dmesg >/dev/null 2>&1; then
  ok "the kernel log is readable (scripts/diagnose.sh reads it)"
else
  warn "this user can't read the kernel log, where a redroid boot failure shows; run scripts/diagnose.sh" \
    "with sudo, or set kernel.dmesg_restrict=0"
fi

echo
if [ "$failures" -gt 0 ]; then
  echo "$failures check(s) failed: fix those before starting redroid."
  exit 1
fi
echo "No failures: start with docker compose pull, then docker compose up -d."
