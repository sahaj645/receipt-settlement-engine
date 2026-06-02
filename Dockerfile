# Backup deploy path. Render uses render.yaml by default; this image
# is here if we ever need to deploy somewhere that wants OCI containers.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# Install deps first so layer caches well between code edits.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Then the app code.
COPY app ./app
COPY static ./static

EXPOSE 8000

# Render injects $PORT; default to 8000 elsewhere.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
