ARG BUILD_FROM
FROM ${BUILD_FROM}

# Runtime deps that stay in the image
RUN apk add --no-cache git tini

# Build deps used only for pip install — removed after.
RUN apk add --no-cache --virtual .build-deps \
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

CMD ["/opt/run.sh"]
