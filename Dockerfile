FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY config ./config
COPY frontend ./frontend
COPY scripts ./scripts

RUN mkdir -p /data

EXPOSE 8000 8501

CMD ["python", "-m", "backend.app.main"]
