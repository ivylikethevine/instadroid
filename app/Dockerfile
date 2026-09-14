FROM python:3.14-slim
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends android-tools-adb curl ca-certificates && rm -rf /var/lib/apt/lists/*
# apkeep fetches the Instagram APK from APKPure when the device doesn't have it installed (see
# _fetch_instagram_apk() in scraper.py) — pinned to a specific release + checksum rather than
# trusting whatever "latest" resolves to at build time.
RUN curl -fsSL -o /usr/local/bin/apkeep \
      https://github.com/EFForg/apkeep/releases/download/1.0.0/apkeep-x86_64-unknown-linux-gnu \
    && echo "a23579a3ba366d25a6d69848189b983d65662f4ecf4b9e11e16510811659de4e  /usr/local/bin/apkeep" | sha256sum -c - \
    && chmod 0755 /usr/local/bin/apkeep
RUN useradd -r -u 1000 -m app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scraper.py app.py entrypoint.sh ./
RUN chmod +x entrypoint.sh
# /db, /media, /debug are bind-mounted by docker-compose in normal use; created here (owned by
# app) so the image also runs standalone with no mounts, e.g. the CI smoke test. /apk likewise
# caches the downloaded Instagram APK (see APK_CACHE_DIR in scraper.py).
RUN mkdir -p /db /media /debug /apk && chown app:app /db /media /debug /apk
USER 1000
CMD ["./entrypoint.sh"]
