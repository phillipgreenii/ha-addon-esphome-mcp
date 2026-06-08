# ESPHome MCP Server — Home Assistant Add-on

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

MCP (Model Context Protocol) server that exposes ESPHome operations as
tools for [Claude Code](https://claude.ai/code). Runs as a Home
Assistant add-on with direct filesystem access — no SSH required.

## Upgrading from 1.0.0?

This release moves ESPHome data from `/config/esphome/` to `/share/esphome/`
and changes the default transport from direct HTTP on port 8099 to HA
ingress. See [esphome-mcp/DOCS.md](esphome-mcp/DOCS.md#migration-from-100)
for the migration steps before you upgrade.

## Quick Start

1. Add this repository as a custom add-on repository in Home Assistant:

   **Settings > Add-ons > Add-on Store > ... (menu) > Repositories**

   ```text
   https://github.com/phillipgreenii/ha-addon-esphome-mcp
   ```

2. Install and start the **ESPHome MCP Server** add-on.

3. Check the add-on logs for the auto-generated auth token (printed once on
   first start).

4. Set `ESPHOME_MCP_TOKEN` in your shell environment.

5. Add to `.mcp.json` in your ESPHome project:

   ```json
   {
     "mcpServers": {
       "esphome": {
         "type": "http",
         "url": "https://<your-ha-host>/api/hassio_ingress/<ingress-token>/mcp/",
         "headers": {
           "Authorization": "Bearer ${ESPHOME_MCP_TOKEN}"
         }
       }
     }
   }
   ```

   (The exact ingress URL is shown in the add-on's "Open Web UI" link; copy
   it from there. Bearer auth is kept as defense-in-depth — HA ingress also
   protects the endpoint with the operator's HA login.)

   > Note: `ESPHOME_MCP_TOKEN` here is the client-side shell variable you choose; the server reads `ESPHOME_MCP_AUTH_TOKEN` inside the container. The two are not required to share a name.

6. Restart Claude Code and verify with `/mcp`.

## Tools

| Tool | Description |
| ---- | ----------- |
| `esphome_list_devices` | List device configs with names |
| `esphome_validate` | Validate a device YAML config |
| `esphome_compile` | Compile firmware for a device |
| `esphome_flash` | OTA flash a device |
| `esphome_logs` | Get recent device logs |
| `esphome_push_files` | Write YAML configs to HA |
| `esphome_pull_files` | Read YAML configs from HA |
| `esphome_push_fonts` | Write font files (base64) to HA |
| `esphome_pull_fonts` | Read font files (base64) from HA |

## Architecture

```text
Claude Code (desktop)  --ingress-->  HA Add-on (MCP Server)  --local-->  ESPHome CLI
                                          |
                                     /share/esphome/  (direct filesystem access)
```

See [esphome-mcp/DOCS.md](esphome-mcp/DOCS.md) for full documentation.

## Security

This add-on grants its operator broad access to ESPHome configuration on the
Home Assistant filesystem and, when enabled, the ability to compile and
OTA-flash ESPHome firmware. Read the threat model before exposing it on a
network you do not fully control.

### What is enforced

- HA ingress is the default transport: TLS + HA login at the edge.
- Bearer token required on every request (defense-in-depth behind ingress).
- Empty token → server refuses all requests (HTTP 503, fail-closed).
- `secrets.compare_digest` for the token.
- All filesystem ops are contained under `/share/esphome/`. Path traversal
  (`..`, absolute paths, symlink escapes including symlinked parent dirs)
  is rejected.
- `secrets.yaml` and `.secret.yaml` cannot be pushed or pulled, even if
  explicitly requested by name.
- Font uploads restricted to `.ttf .otf .bdf .pcf .woff .woff2`.
- Configurable request-body size cap (default 8 MiB) enforced before auth;
  chunked transfer encoding is refused.
- Per-file size cap (default 1 MiB) on push.
- `esphome_compile` and `esphome_flash` are **disabled by default**.
- Concurrent compile/flash invocations bounded by `max_concurrent_compiles`.
- Container runs as unprivileged `esphomemcp` (UID 10001) under `tini` for
  signal handling, with an AppArmor profile.
- Supervisor watchdog (`/health`) restarts the add-on if it hangs.
- Token logged once on first generation; stored at `/data/auth_token` mode
  0600.

### Mitigated, not eliminated

- **`compile_enabled` enables RCE in the add-on container.** ESPHome
  `platformio_options` accepts `extra_scripts`, which run arbitrary Python
  during build. Only enable compile if every holder of the bearer token is
  trusted to execute code inside the container.
- **`flash_enabled` enables OTA flashing of any device whose OTA password
  appears in `secrets.yaml`.** ESPHome reads the password directly; the MCP
  server never sees it, so a per-device allowlist is not implemented in
  this release.
- **Direct port 8099** is not exposed by default. If you re-enable it in
  `config.yaml` (`ports: 8099/tcp: 8099`), the bearer token traverses
  plaintext HTTP on the LAN unless you also front it with TLS.
- **Token at rest.** Plaintext on disk at mode 0600. `/data/auth_token` is
  included in Supervisor backups; if you restore a snapshot onto a different
  HA instance, rotate the token.

### Recommended deployment

- Leave `compile_enabled` and `flash_enabled` at their defaults (`false`).
- Use ingress; do not re-add direct port mapping.
- Rotate the token after any Supervisor backup restore to a new host:
  delete `/data/auth_token` and restart the add-on.

## License

[MIT](LICENSE) — Original copyright 2026 Berrevoets Systems; 1.1.0 hardening fork maintained by phillipgreenii.
