FROM python:3.14-slim
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
# apkeep fetches the Instagram APK from APKPure when the device doesn't have it installed (see
# instadroid/install.py) — pinned to a specific release + checksum rather than trusting whatever
# "latest" resolves to at build time. ADD verifies the checksum itself, so no curl in the image.
ADD --checksum=sha256:a23579a3ba366d25a6d69848189b983d65662f4ecf4b9e11e16510811659de4e --chmod=0755 \
    https://github.com/EFForg/apkeep/releases/download/1.0.0/apkeep-x86_64-unknown-linux-gnu \
    /usr/local/bin/apkeep
RUN useradd -r -u 1000 -m app
WORKDIR /app
COPY requirements.txt .
# adbutils ships Google's platform-tools adb (only needs glibc); link it onto PATH instead of
# installing Debian's older android-tools-adb and its dependencies.
RUN pip install --no-cache-dir -r requirements.txt \
    && ln -s "$(python -c 'import adbutils; print(adbutils.adb_path())')" /usr/local/bin/adb \
    && adb version
COPY scraper.py app.py fileenv.py entrypoint.sh ./
COPY instadroid ./instadroid
COPY igprofiles ./igprofiles
RUN chmod +x entrypoint.sh
# /db, /media, /debug are bind-mounted by docker-compose in normal use; created here (owned by
# app) so the image also runs standalone with no mounts, e.g. the CI smoke test. /apk likewise
# caches the downloaded Instagram APK (see APK_CACHE_DIR in instadroid/config.py).
RUN mkdir -p /db /media /debug /apk && chown app:app /db /media /debug /apk
USER 1000
CMD ["./entrypoint.sh"]
