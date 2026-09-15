# Pinned by digest (Dependabot's docker updates move it); the tag says which image it is.
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6
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
ADD --checksum=sha256:a23579a3ba366d25a6d69848189b983d65662f4ecf4b9e11e16510811659de4e --chmod=0755 \
    https://github.com/EFForg/apkeep/releases/download/1.0.0/apkeep-x86_64-unknown-linux-gnu \
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
# The same probe as docker-compose.yml's healthcheck, for the image run on its own: /health answers 503
# once scraping looks stuck or keeps failing.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as u; u.urlopen('http://localhost:' + os.environ.get('FEED_PORT', '8000') + '/health', timeout=3)"]
CMD ["./entrypoint.sh"]
