FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends android-tools-adb && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scraper.py .
CMD ["python", "-u", "scraper.py"]
