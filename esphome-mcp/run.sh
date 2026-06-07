#!/usr/bin/with-contenv bashio
# ==============================================================================
# ESPHome MCP Server — Add-on entry point
# ==============================================================================
set -e

# Ensure /share/esphome and /data are present and writable by the runtime user.
# This runs as root (no USER directive in Dockerfile); we drop privs at exec.
mkdir -p /share/esphome /share/esphome/archive /share/esphome/fonts
chown -R esphomemcp:esphomemcp /share/esphome /data 2>/dev/null || true

if bashio::config.has_value 'auth_token'; then
    AUTH_TOKEN="$(bashio::config 'auth_token')"
else
    TOKEN_FILE="/data/auth_token"
    if [ ! -f "$TOKEN_FILE" ]; then
        AUTH_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
        umask 077
        printf '%s\n' "$AUTH_TOKEN" > "$TOKEN_FILE"
        chmod 600 "$TOKEN_FILE"
        bashio::log.warning "==================================================="
        bashio::log.warning "  Generated MCP Auth Token (printed ONCE):"
        bashio::log.warning "    ${AUTH_TOKEN}"
        bashio::log.warning "  Copy it now. To rotate, delete /data/auth_token"
        bashio::log.warning "  and restart the add-on. NOTE: /data is included"
        bashio::log.warning "  in Supervisor backups."
        bashio::log.warning "==================================================="
    else
        AUTH_TOKEN="$(cat "$TOKEN_FILE")"
        chmod 600 "$TOKEN_FILE" || true
        bashio::log.info "Auth token loaded from /data/auth_token"
    fi
fi

# Idiomatic boolean parsing
if bashio::config.true 'compile_enabled'; then
    COMPILE_ENABLED=true
else
    COMPILE_ENABLED=false
fi
if bashio::config.true 'flash_enabled'; then
    FLASH_ENABLED=true
else
    FLASH_ENABLED=false
fi

MAX_CONCURRENT_COMPILES="$(bashio::config 'max_concurrent_compiles')"
MAX_BODY_MB="$(bashio::config 'max_body_mb')"
MAX_FILE_MB="$(bashio::config 'max_file_mb')"

export ESPHOME_MCP_AUTH_TOKEN="$AUTH_TOKEN"
export ESPHOME_MCP_COMPILE_ENABLED="$COMPILE_ENABLED"
export ESPHOME_MCP_FLASH_ENABLED="$FLASH_ENABLED"
export ESPHOME_MCP_MAX_CONCURRENT_COMPILES="$MAX_CONCURRENT_COMPILES"
export ESPHOME_MCP_MAX_BODY_BYTES="$((MAX_BODY_MB * 1024 * 1024))"
export ESPHOME_MCP_MAX_FILE_BYTES="$((MAX_FILE_MB * 1024 * 1024))"
export ESPHOME_DIR="/share/esphome"
export MCP_BIND="${MCP_BIND:-0.0.0.0}"

bashio::log.info "compile_enabled=${COMPILE_ENABLED} flash_enabled=${FLASH_ENABLED}"
bashio::log.info "max_concurrent_compiles=${MAX_CONCURRENT_COMPILES} body_mb=${MAX_BODY_MB} file_mb=${MAX_FILE_MB}"
bashio::log.info "Starting ESPHome MCP Server on ${MCP_BIND}:8099 (ESPHOME_DIR=${ESPHOME_DIR})"

# Drop privileges and let tini handle PID 1 (signal forwarding + reaping).
exec s6-setuidgid esphomemcp tini -g -- python3 -m server.main
