# ESPHome MCP Server

This add-on runs an MCP (Model Context Protocol) server that exposes
ESPHome operations as tools for Claude Code. It runs directly on your
Home Assistant instance with native filesystem access to
`/config/esphome/` — no SSH tunneling required.

## Architecture

```text
Claude Code (desktop)  --HTTP-->  HA Add-on (MCP Server)  --local-->  ESPHome CLI
                                       |
                                  /config/esphome/  (direct filesystem access)
```

## Configuration

### auth_token

An authentication token to secure the MCP endpoint. If left empty, a
token is auto-generated on first start and printed in the add-on logs.

You can set your own token in the add-on configuration:

```yaml
auth_token: "my-secret-token"
```

## Setup

1. Add this repository as a custom add-on repository in Home Assistant:
   **Settings > Add-ons > Add-on Store > ... > Repositories**
   Enter: `https://github.com/bberrevoets/ha-addon-esphome-mcp`

2. Install the **ESPHome MCP Server** add-on and start it.

3. Check the add-on logs for the auth token (if you didn't set one).

4. Set the `ESPHOME_MCP_TOKEN` environment variable on your development
   machine to the auth token value.

5. Configure `.mcp.json` in your ESPHome project:

   ```json
   {
     "mcpServers": {
       "esphome": {
         "type": "http",
         "url": "http://<your-ha-host>:8099/mcp",
         "headers": {
           "Authorization": "Bearer ${ESPHOME_MCP_TOKEN}"
         }
       }
     }
   }
   ```

6. Restart Claude Code and verify the connection with `/mcp`.

## Available Tools

| Tool | Description |
| ---- | ----------- |
| `esphome_list_devices` | List device configs with names |
| `esphome_validate` | Validate a device YAML config |
| `esphome_compile` | Compile firmware for a device |
| `esphome_flash` | OTA flash a device |
| `esphome_logs` | Get recent device logs (snapshot) |
| `esphome_push_files` | Write YAML files to the config directory |
| `esphome_pull_files` | Read YAML files from the config directory |
| `esphome_push_fonts` | Write font files (base64-encoded) |
| `esphome_pull_fonts` | Read font files (base64-encoded) |

## Configuration reference

| Option | Default | Description |
| ------ | ------- | ----------- |
| `auth_token` | (auto) | Bearer token (HA UI masks the field). Auto-generated and shown in logs on first start only. Changes require an add-on restart. |
| `compile_enabled` | `false` | Allow `esphome_compile`. Equivalent to allowing remote code execution in the add-on container. Changes require a restart. |
| `flash_enabled` | `false` | Allow `esphome_flash` (OTA firmware push to devices). Changes require a restart. |
| `max_concurrent_compiles` | `1` | Cap on concurrent compile/flash invocations (1-8). |
| `max_body_mb` | `8` | Maximum request body size (1-64 MiB). Enforced before auth. |
| `max_file_mb` | `1` | Maximum size for a single pushed file (1-16 MiB). |

### Multi-arch availability

This add-on ships `aarch64` and `amd64` images. `armv7` and `armhf` (Pi 3,
Pi Zero 2 W) are not supported in this release because the ESPHome wheel set
is significantly larger to build on 32-bit Alpine; PRs welcome.

### Migration from 1.0.0

Data root moved from `/config/esphome/` to `/share/esphome/`. After
upgrading, move your YAML configs:

```bash
# From the HA host shell or SSH add-on:
mkdir -p /share/esphome
mv /config/esphome/* /share/esphome/
```

The default transport also changed from direct HTTP on port 8099 to HA
ingress. Update your MCP client's `mcp.json` to use the ingress URL shown
in the add-on's "Open Web UI" link.

### Future work (not in this release)

- Pre-built images published to GHCR (so installs do not require a local build).
- `icon.png` and `logo.png` for the add-on store.
- HA ingress session-based auth as an alternative to the bearer token.
- Per-device allowlist for flash to constrain OTA blast radius.

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

## Network

The add-on listens on port **8099** (TCP). Make sure this port is
accessible from your development machine.
