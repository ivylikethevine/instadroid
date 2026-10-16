# Pinned by digest (tool-versions.yml reports when the tag moves); the tag says which image it is.
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
# Debian's security fixes land before the python image is rebuilt with them (Trivy flagged sqlite, pcre2,
# gzip and perl-base), and the scraper and feed server both use the system libsqlite3.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*
# apkeep fetches the Instagram APK from APKPure when the device doesn't have it installed (see
# instadroid/install.py) — pinned to a specific release + checksum rather than trusting whatever
# "latest" resolves to at build time. ADD verifies the checksum itself, so no curl in the image.
# .github/workflows/new-builds.yml reads its apkeep URL and checksum from this ADD.
ADD --checksum=sha256:badd7ad9fa7d2f32abe01eaf33b7ff086361a95bbc000a33bf44b630d4bf2140 --chmod=0755 \
    https://github.com/EFForg/apkeep/releases/download/1.1.0/apkeep-x86_64-unknown-linux-gnu \
    /usr/local/bin/apkeep
RUN useradd -r -u 1000 -m app
WORKDIR /app
# The hashed lock compiled from requirements.in (pip-compile), so every build installs the same files.
COPY requirements.txt .
# adbutils ships Google's platform-tools adb (only needs glibc); link it onto PATH instead of
# installing Debian's older android-tools-adb and its dependencies. pip itself is removed afterwards:
# nothing at runtime uses it, and the copies of msgpack and pkg_resources it vendors carry CVEs.
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && ln -s "$(python -c 'import adbutils; print(adbutils.adb_path())')" /usr/local/bin/adb \
    && adb version \
    && python -m pip uninstall --yes pip
COPY scraper.py ./
COPY --chmod=0755 entrypoint.sh ./
COPY shared ./shared
COPY feedserver ./feedserver
COPY instadroid ./instadroid
COPY igprofiles ./igprofiles
# /db, /media, /debug are bind-mounted by docker-compose in normal use; created here (owned by
# app) so the image also runs standalone with no mounts, e.g. the CI smoke test. /apk likewise
# caches the downloaded Instagram APK (see APK_CACHE_DIR in instadroid/config.py).
RUN mkdir -p /db /media /debug /apk && chown app:app /db /media /debug /apk
USER 1000
# docker-compose.yml defines no healthcheck, so this one covers the compose service and the image run on
# its own alike: /health answers 503 once scraping looks stuck or keeps failing.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as u; u.urlopen('http://localhost:' + os.environ.get('FEED_PORT', '8000') + '/health', timeout=3)"]
CMD ["./entrypoint.sh"]

LABEL org.opencontainers.image.source=https://github.com/ivylikethevine/instadroid
LABEL org.opencontainers.image.description="python slim based installation of instadroid, an instagram to rss converter using an android emulator"
LABEL org.opencontainers.image.licenses=MIT
