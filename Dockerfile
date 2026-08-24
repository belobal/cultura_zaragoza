FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EVENT_CACHE_TTL_SECONDS=3600

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Writable cache for scrapers / geocode on ephemeral filesystem
RUN mkdir -p cache

EXPOSE 5000
ENV PORT=5000

CMD gunicorn --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 180 "app:create_app()"
