FROM python:3.14-slim
RUN useradd -r -u 1000 -m feed
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
USER feed
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
