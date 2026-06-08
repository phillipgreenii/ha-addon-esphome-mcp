ARG BUILD_FROM
FROM ${BUILD_FROM}

# Runtime deps that stay in the image, plus build deps used only for pip
# install (the `.build-deps` virtual package is removed below after pip).
# Version pinning intentionally skipped — see .hadolint.yaml (DL3018).
RUN apk add --no-cache git \
 && apk add --no-cache --virtual .build-deps \
    gcc \
    g++ \
    musl-dev \
    libffi-dev \
    openssl-dev \
    rust \
    cargo

# Create the unprivileged runtime user with a fixed UID/GID.
# We do NOT set USER — Supervisor needs root to make /data and /share/esphome
# writable. Privileges are dropped at exec time via s6-setuidgid in run.sh.
RUN addgroup -S esphomemcp -g 10001 \
 && adduser -S -u 10001 -G esphomemcp -h /opt esphomemcp

# Install Python dependencies (hashes pinned).
# Lockfile is per-arch; pick the right one via BUILD_ARCH.
ARG BUILD_ARCH
COPY requirements.${BUILD_ARCH}.lock /tmp/requirements.lock
RUN pip3 install --no-cache-dir --require-hashes -r /tmp/requirements.lock \
 && rm /tmp/requirements.lock \
 && apk del .build-deps

# Copy server code
COPY server/ /opt/server/
COPY run.sh /opt/

RUN chmod a+x /opt/run.sh \
 && chown -R esphomemcp:esphomemcp /opt

WORKDIR /opt

# Supervisor watchdog: HEALTHCHECK lets Home Assistant restart us if /health
# stops responding. Replaces the deprecated YAML `watchdog:` key (which the
# addon-linter now rejects).
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
  CMD python3 -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8099/health', timeout=3).status == 200 else 1)" || exit 1

CMD ["/opt/run.sh"]
