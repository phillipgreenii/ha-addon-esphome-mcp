# CLAUDE.md

This file provides guidance to Claude Code when working with code in
this repository.

## Project Overview

Home Assistant custom add-on that runs an MCP (Model Context Protocol)
server for ESPHome operations. Claude Code connects to it over HA ingress
(HTTPS + HA-login auth + bearer token defense-in-depth), gets direct access
to the ESPHome CLI and `/share/esphome/`.

## Repository Structure

- `repository.yaml` — HA add-on repository metadata.
- `esphome-mcp/` — The add-on.
  - `config.yaml` — HA add-on manifest. Ingress is the default transport
    (`ingress: true`, `ingress_entry: /mcp`); `map: [{type: share,
    read_only: false}]` (NOT `config:rw`); `watchdog: ...:/health`
    exposes the Supervisor "Watchdog" toggle.
  - `build.yaml` — Multi-arch Docker build config. NO `args:` block.
  - `Dockerfile` — Alpine + Python + ESPHome. Container runs as
    root only long enough to chown; drops to UID 10001 via `s6-setuidgid`
    in `run.sh`.
  - `run.sh` — Add-on entry point. Reads options via bashio (`config.true`,
    `config.has_value`). Final exec is
    `s6-setuidgid esphomemcp python3 -m server.main`.
  - `requirements.txt.in` — Declared top-level Python deps.
  - `requirements.{amd64,aarch64}.lock` — Hash-pinned per-arch lockfiles.
    Regenerate with `uv pip compile ... --generate-hashes`.
  - `apparmor.txt` — Profile name MUST equal slug `esphome-mcp`.
  - `server/` — Python package.
    - `main.py` — FastMCP app, tool registration, uvicorn entry point.
      Wires `BodySizeLimitMiddleware` (outer) and `BearerAuthMiddleware`
      (inner). Registers `/health` route directly on the Starlette app.
    - `tools.py` — Tool implementations. compile_device/flash are `async`;
      no sync wrappers. All filesystem paths flow through
      `server.paths.safe_join`/`safe_filename`.
    - `paths.py` — Containment helpers (walks parents to catch symlinked
      directory escapes).
    - `auth.py` — Bearer auth. Fail-closed on empty token. Constant-time
      compare. Reads env at request time; rotation does NOT require
      reload.
    - `config.py` — Runtime settings dataclass from env vars.
    - `limits.py` — `BodySizeLimitMiddleware` + per-loop semaphore cache
      (`get_compile_semaphore`).
    - `health.py` — `/health` endpoint for Supervisor watchdog.
  - `DOCS.md` — Add-on documentation page shown in HA UI.

## Key Conventions

- **Transport**: HA ingress (`ingress: true`). The internal port is the
  Supervisor default (8099); `ingress_port:` is intentionally omitted
  because the addon-linter rejects keys set to their schema default.
  Direct LAN port deliberately not exposed in the default `config.yaml`.
- **Auth**: Bearer token in `Authorization` header. Auto-generated on
  first start, persisted plaintext at `/data/auth_token` mode 0600,
  printed once. Fail-closed: server refuses all non-`GET /health` requests
  when the env var is empty. Comparison via `secrets.compare_digest`.
- **Data root**: `/share/esphome/` (NOT `/config/esphome/`). All user-
  supplied paths go through `server.paths.safe_join` or `safe_filename`;
  never call `os.path.join(ESPHOME_DIR, user_input)` directly in `tools.py`.
- **Compile/flash**: opt-in via add-on options. Async functions, gated by
  a loop-scoped semaphore. When enabled they are documented as RCE/OTA
  surfaces.
- **Secrets**: `secrets.yaml`/`.secret.yaml` rejected by basename in
  push/pull. Best-effort UX filter, not a security boundary.
- **ESPHome**: installed at build time via pip with hash-pinned wheels
  (`--require-hashes`). Per-arch lockfile.
- **Container**: runs as UID 10001 (`esphomemcp`) under s6-overlay. AppArmor
  profile name = slug `esphome-mcp`.

## Building / Testing

The add-on is built by HA Supervisor when installed. For local testing:

```bash
cd esphome-mcp
docker build \
  --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.21 \
  --build-arg BUILD_ARCH=amd64 \
  -t esphome-mcp .
docker run --rm -p 8099:8099 \
  -v /path/to/share:/share \
  -e ESPHOME_MCP_AUTH_TOKEN=test \
  esphome-mcp
```

Run the test suite:

```bash
uv sync --dev
uv run pytest -v
```

## Deployment

Add `https://github.com/phillipgreenii/ha-addon-esphome-mcp` as a custom
add-on repository in Home Assistant, then install and start the add-on.
