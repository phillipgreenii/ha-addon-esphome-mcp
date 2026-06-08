# Changelog

All notable changes to this project will be documented in this file.

## Attributors

- **Bert Berrevoets** — Project author
- **Claude Code** — AI-assisted development
- **phillipgreenii** — 1.1.0 security hardening + HA-conventions fork

## [1.1.0] - 2026-06-07

### Action required on upgrade

- **Move ESPHome configs.** Data root changed from `/config/esphome/` to
  `/share/esphome/`. Move your existing YAML and font files before
  starting v1.1.0; see DOCS.md "Migration from 1.0.0" for the exact
  commands. Until you migrate, `esphome_list_devices` will return empty.
- **Update MCP client URL.** Default transport switched to HA ingress.
  Your `.mcp.json` must point at the ingress URL shown in the add-on's
  "Open Web UI" link, not `http://<host>:8099/mcp`.
- **Rotate auth token if restored from backup.** `/data/auth_token` is
  included in Supervisor backups. If you restored a snapshot onto a new
  HA instance, delete `/data/auth_token` and restart the add-on.

### Security

This release closes the findings from a third-party review of v1.0.0.
Operators should update and rotate their auth tokens.

#### Fixed
- **Critical:** Path traversal in `push_files` / `pull_files`
  (could overwrite or read arbitrary `.yaml` under `/config`). Data root
  also moved to `/share/esphome` so the Supervisor mount is narrower.
- **Critical:** Sanitized YAML parse exceptions; FS paths no longer leak
  to clients.
- **High:** Auth token logged once on first generation, not on every boot.
  Stored at `/data/auth_token` mode 0600.
- **High:** Server fails closed (HTTP 503) when no auth token is configured.
- **High:** Constant-time bearer token comparison.
- **High:** Body-size cap (rejects chunked transfer encoding bypass).
- **High:** `_device_yaml_path` no longer accepts absolute paths or `..`.
- **Medium:** Font uploads restricted to known font extensions and base64
  validation.
- **Medium:** Per-file size limits.
- **Medium:** Concurrent compile/flash invocations bounded by a
  loop-scoped semaphore.
- **Medium:** Container drops to unprivileged `esphomemcp` (UID 10001) via
  `s6-setuidgid`. PID 1 is the HA base image's s6-overlay supervisor.
- **Medium:** AppArmor profile shipped (profile name matches slug).
- **Medium:** Runtime dependencies pinned with hash verification (per-arch
  lockfiles).

#### Mitigated, not eliminated
- `compile`/`flash` default to disabled. When enabled they remain a
  remote-code-execution surface via PlatformIO `extra_scripts`. See
  README "Security" for the explicit threat model.
- OTA flash, when enabled, can reach any device whose OTA password is in
  `secrets.yaml`.

### Added
- `compile_enabled`, `flash_enabled`, `max_concurrent_compiles`,
  `max_body_mb`, `max_file_mb` add-on options.
- `/health` endpoint exposed for Supervisor's Watchdog toggle and as a
  Docker `HEALTHCHECK` defense-in-depth (the HEALTHCHECK probes
  `http://127.0.0.1:8099/health` from inside the container).
- HA ingress as the default transport (`ingress: true`); direct port 8099
  removed from default config.
- pytest suite with regression tests for every fix.
- GitHub Actions CI: pytest, per-arch lockfile drift, add-on linter,
  hadolint.

### Changed
- `auth_token` option type changed from `str` to `password` (HA UI masks it).
- `map: config:rw` → `map: share:rw` (ESPHome data lives in
  `/share/esphome` now).
- `init: false` removed (defaults to true; the HA base image's s6-overlay
  is PID 1 and handles signal forwarding + zombie reaping).
- `panel_icon` removed (was dead config; ingress now provides the side
  panel entry).
- Subprocess invocations (`esphome compile`/`run`/`config`/`logs`) now
  spawn with `start_new_session=True` and are signaled via
  `os.killpg()` on timeout or client disconnect, so grandchildren
  (`platformio`, `gcc`, …) are reaped properly.
- `_scan_unsafe_includes` now parses pushed YAML with a
  `yaml.SafeLoader` subclass + `add_multi_constructor` rather than a
  regex, closing two verified-live exfiltration bypasses (quoted
  escape-newline scalars and the `!include` mapping form). Also rejects
  YAML documents containing `%TAG` / `%YAML` directives and the
  sequence form `!include\n  - <path>`.

### Removed
- Dead `host="0.0.0.0"` kwarg on `FastMCP(...)`.
- Hardcoded `BUILD_ARCH: aarch64` in `build.yaml` (Supervisor injects
  per-arch automatically).

## [1.0.0] - 2026-03-17

### Added

Author: *Bert Berrevoets, Claude Code*

- Initial release as Home Assistant add-on
- FastMCP server with streamable HTTP transport on port 8099
- Bearer token authentication (auto-generated or user-configured)
- Nine MCP tools: list_devices, validate, compile, flash, logs,
  push_files, pull_files, push_fonts, pull_fonts
- Direct filesystem access to `/config/esphome/` — no SSH required
- Alpine-based Docker image with ESPHome and PlatformIO pre-installed
- Multi-architecture support (aarch64, amd64)
- Add-on documentation (DOCS.md)
- secrets.yaml protection in push/pull operations
