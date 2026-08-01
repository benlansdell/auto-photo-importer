FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gphoto2 \
    libgphoto2-6 \
    libgphoto2-port12 \
    libimage-exiftool-perl \
    rsync \
    sqlite3 \
    usbutils \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md config.example.yaml ./
COPY src/ src/
COPY hooks/ hooks/

RUN pip install --no-cache-dir .

RUN mkdir -p /photos /db /logs /config /tmp/xt3-import \
    && chmod +x /app/hooks/*.sh || true

ENV PHOTO_IMPORTER_CONFIG=/config/config.yaml
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1

CMD ["python", "-m", "photo_importer"]
