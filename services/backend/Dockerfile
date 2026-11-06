# Build a partir de services/:  docker build -f backend/Dockerfile -t backend .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY backend/requirements.txt .
RUN pip install -r requirements.txt

COPY comum/ comum/
COPY backend/main.py .

# 1 worker: a marcação de cold start é por processo
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
