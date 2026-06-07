#!/usr/bin/with-contenv bashio
# ==============================================================================
# ESPHome MCP Server — Add-on entry point
# ==============================================================================
set -e

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

export ESPHOME_MCP_AUTH_TOKEN="$AUTH_TOKEN"
export ESPHOME_DIR="/config/esphome"

bashio::log.info "Starting ESPHome MCP Server on port 8099..."
exec python3 -m server.main
