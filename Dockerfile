FROM python:3.12-slim

# TARGETARCH is only set automatically by BuildKit; the legacy builder leaves it
# empty, so it is always read with a dpkg fallback below.
ARG TARGETARCH=""
ARG GPHOTOS_UPLOADER_VERSION=5.1.0

RUN apt-get update && apt-get install -y --no-install-recommends \
    gphoto2 \
    libgphoto2-6 \
    libgphoto2-port12 \
    libimage-exiftool-perl \
    rsync \
    sqlite3 \
    usbutils \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# JPEG → Google Photos (called from hooks/post_import.sh)
# v5.1.0 only publishes a linux_amd64 binary. On other architectures the build
# still succeeds and post_import.sh reports the upload as disabled.
RUN set -eux; \
    arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    if [ "$arch" != "amd64" ]; then \
      echo "gphotos-uploader-cli: no linux_${arch} build in v${GPHOTOS_UPLOADER_VERSION}; Google Photos upload disabled in this image"; \
    else \
      url="https://github.com/gphotosuploader/gphotos-uploader-cli/releases/download/v${GPHOTOS_UPLOADER_VERSION}/gphotos-uploader-cli_${GPHOTOS_UPLOADER_VERSION}_linux_${arch}.tar.gz"; \
      mkdir -p /tmp/gphotos; \
      curl -fsSL "$url" -o /tmp/gphotos.tgz; \
      tar -xzf /tmp/gphotos.tgz -C /tmp/gphotos; \
      binary="$(find /tmp/gphotos -type f -name gphotos-uploader-cli | head -n 1)"; \
      test -n "$binary"; \
      install -m 0755 "$binary" /usr/local/bin/gphotos-uploader-cli; \
      rm -rf /tmp/gphotos /tmp/gphotos.tgz; \
      gphotos-uploader-cli --help >/dev/null; \
    fi

WORKDIR /app

COPY pyproject.toml README.md config.example.yaml ./
COPY src/ src/
COPY hooks/ hooks/
COPY scripts/ scripts/

RUN pip install --no-cache-dir .

RUN mkdir -p /photos /db /logs /config /gphotos /tmp/xt3-import \
    && chmod +x /app/hooks/*.sh || true

ENV PHOTO_IMPORTER_CONFIG=/config/config.yaml
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1

CMD ["python", "-m", "photo_importer"]
