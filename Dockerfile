FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY samples ./samples

RUN mkdir -p /app/storage

ENV PYTHONPATH=/app
ENV STORAGE_ROOT=/app/storage
ENV UVICORN_WORKERS=3
ENV MPLCONFIGDIR=/tmp/matplotlib

EXPOSE 8000

# Workers configurables (default 3) para perfil de 7 GB API / 12 GB stack
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-3} --timeout-keep-alive 30 --limit-concurrency 200"]

