ARG BUILD_FROM
FROM ${BUILD_FROM}

# Build deps for ESPHome native pieces during pip install
RUN apk add --no-cache \
    gcc \
    g++ \
    musl-dev \
    libffi-dev \
    openssl-dev \
    rust \
    cargo \
    git \
    tini

# Create the unprivileged runtime user with a fixed UID/GID.
# We do NOT set USER — Supervisor needs root to make /data and /share/esphome
# writable. Privileges are dropped at exec time via s6-setuidgid in run.sh.
RUN addgroup -S esphomemcp -g 10001 \
 && adduser -S -u 10001 -G esphomemcp -h /opt esphomemcp

# Install Python dependencies (hashes pinned, see Task 19).
# Lockfile is per-arch; pick the right one via BUILD_ARCH.
ARG BUILD_ARCH
COPY requirements.${BUILD_ARCH}.lock /tmp/requirements.lock
RUN pip3 install --no-cache-dir --require-hashes -r /tmp/requirements.lock \
 && rm /tmp/requirements.lock

# Copy server code
COPY server/ /opt/server/
COPY run.sh /opt/

RUN chmod a+x /opt/run.sh \
 && chown -R esphomemcp:esphomemcp /opt

WORKDIR /opt

CMD ["/opt/run.sh"]
