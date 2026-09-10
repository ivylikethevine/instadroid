FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends android-tools-adb && rm -rf /var/lib/apt/lists/*
RUN useradd -r -u 1000 -m app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scraper.py app.py entrypoint.sh ./
RUN chmod +x entrypoint.sh
# /db, /media, /debug are bind-mounted by docker-compose in normal use; created here (owned by
# app) so the image also runs standalone with no mounts, e.g. the CI smoke test.
RUN mkdir -p /db /media /debug && chown app:app /db /media /debug
USER app
CMD ["./entrypoint.sh"]
