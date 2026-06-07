"""ESPHome MCP tool implementations.

All tools operate locally on the Home Assistant filesystem — no SSH needed.
"""

import asyncio
import base64
import glob
import logging
import os
import subprocess

import yaml

log = logging.getLogger("esphome-mcp")

ESPHOME_DIR = os.environ.get("ESPHOME_DIR", "/config/esphome")
ESPHOME_BIN = "esphome"

FORBIDDEN_FILES = {"secrets.yaml", ".secret.yaml"}

ALLOWED_FONT_EXTENSIONS = {".ttf", ".otf", ".bdf", ".pcf", ".woff", ".woff2"}

_DISABLED_MSG = (
    "{action} is disabled. Enable it by setting the add-on option "
    "{option} to true (see DOCS.md for security implications). Changes "
    "take effect after the add-on is restarted."
)


def _is_allowed_font(name: str) -> bool:
    return any(name.lower().endswith(ext) for ext in ALLOWED_FONT_EXTENSIONS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _resolve_device(device: str) -> str:
    from .paths import ContainmentError, safe_filename

    try:
        # Validate the raw device name first so callers can't sneak in "."
        # or ".." (which would otherwise become "..yaml" / "...yaml" after
        # the suffix append and silently pass safe_filename).
        safe_filename(device)
        name = device if device.endswith(".yaml") else f"{device}.yaml"
        return safe_filename(name)
    except ContainmentError as e:
        raise ValueError(f"invalid device name: {device!r}") from e


def _device_yaml_path(device: str) -> str:
    """Resolve a device name to a contained YAML path. Raises ValueError
    on traversal attempts."""
    from .paths import safe_join

    filename = _resolve_device(device)
    primary = safe_join(ESPHOME_DIR, filename)
    if os.path.isfile(primary):
        return primary
    archive = safe_join(ESPHOME_DIR, os.path.join("archive", filename))
    if os.path.isfile(archive):
        return archive
    return primary


def _run(cmd: list[str], timeout: int = 120, cwd: str | None = None) -> str:
    """Run a command and return combined stdout+stderr."""
    log.info("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or ESPHOME_DIR,
        )
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr
        output = output.strip()
        if result.returncode != 0:
            return f"Command failed (exit {result.returncode}):\n{output}"
        return output
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
    except FileNotFoundError as e:
        return f"Command not found: {e}"


def _parse_device_info(yaml_path: str) -> dict:
    """Parse basic device info from a YAML file. Errors are summarized;
    full exception detail is never returned to the client."""
    try:
        with open(yaml_path, encoding="utf-8") as f:
            class SecretLoader(yaml.SafeLoader):
                pass

            def secret_constructor(loader, node):
                return f"!secret {loader.construct_scalar(node)}"

            SecretLoader.add_constructor("!secret", secret_constructor)
            data = yaml.load(f, Loader=SecretLoader)

        esphome_section = (data or {}).get("esphome", {})
        return {
            "name": esphome_section.get("name", "unknown"),
            "friendly_name": esphome_section.get("friendly_name", ""),
            "file": os.path.basename(yaml_path),
        }
    except Exception:
        log.exception("parse failed for %s", os.path.basename(yaml_path))
        return {
            "name": "error",
            "friendly_name": "",
            "file": os.path.basename(yaml_path),
            "error": "could not parse YAML",
        }


def _is_forbidden(filename: str) -> bool:
    """Check if a filename is forbidden for transfer."""
    return os.path.basename(filename).lower() in FORBIDDEN_FILES


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------
def list_devices() -> str:
    """List all available ESPHome device configurations."""
    devices = []

    for path in sorted(glob.glob(os.path.join(ESPHOME_DIR, "*.yaml"))):
        if _is_forbidden(path):
            continue
        info = _parse_device_info(path)
        info["status"] = "active"
        devices.append(info)

    archive_dir = os.path.join(ESPHOME_DIR, "archive")
    if os.path.isdir(archive_dir):
        for path in sorted(glob.glob(os.path.join(archive_dir, "*.yaml"))):
            info = _parse_device_info(path)
            info["status"] = "archived"
            devices.append(info)

    if not devices:
        return "No device configurations found."

    lines = ["ESPHome Devices:", ""]
    for d in devices:
        name = d["name"]
        friendly = f' ("{d["friendly_name"]}")' if d.get("friendly_name") else ""
        status = f" [{d['status']}]" if d["status"] == "archived" else ""
        error = f" ERROR: {d['error']}" if d.get("error") else ""
        lines.append(f"  - {name}{friendly}{status} ({d['file']}){error}")

    return "\n".join(lines)


def validate(device: str) -> str:
    try:
        yaml_path = _device_yaml_path(device)
    except ValueError as e:
        return f"invalid device name (rejected by safety check): {e}"
    if not os.path.isfile(yaml_path):
        return f"Device config not found: {os.path.basename(yaml_path)}"
    return _run([ESPHOME_BIN, "config", yaml_path])


async def compile_device(device: str) -> str:
    """Compile ESPHome firmware for a device. Async; semaphore-gated."""
    from .config import settings
    from .limits import get_compile_semaphore

    if not settings.compile_enabled:
        return _DISABLED_MSG.format(action="compile", option="compile_enabled")
    try:
        yaml_path = _device_yaml_path(device)
    except ValueError as e:
        return f"invalid device name (rejected by safety check): {e}"
    if not os.path.isfile(yaml_path):
        return f"Device config not found: {os.path.basename(yaml_path)}"

    sem = get_compile_semaphore()
    async with sem:
        return await asyncio.to_thread(
            _run, [ESPHOME_BIN, "compile", yaml_path], timeout=300
        )


async def flash(device: str) -> str:
    """OTA flash a device. Async; semaphore-gated."""
    from .config import settings
    from .limits import get_compile_semaphore

    if not settings.flash_enabled:
        return _DISABLED_MSG.format(action="flash", option="flash_enabled")
    try:
        yaml_path = _device_yaml_path(device)
    except ValueError as e:
        return f"invalid device name (rejected by safety check): {e}"
    if not os.path.isfile(yaml_path):
        return f"Device config not found: {os.path.basename(yaml_path)}"

    sem = get_compile_semaphore()
    async with sem:
        return await asyncio.to_thread(
            _run, [ESPHOME_BIN, "run", yaml_path, "--no-logs"], timeout=600
        )


def logs(device: str, num_lines: int = 50) -> str:
    try:
        yaml_path = _device_yaml_path(device)
    except ValueError as e:
        return f"invalid device name (rejected by safety check): {e}"
    if not os.path.isfile(yaml_path):
        return f"Device config not found: {os.path.basename(yaml_path)}"
    output = _run(
        ["timeout", "15", ESPHOME_BIN, "logs", yaml_path],
        timeout=30,
    )
    lines = output.splitlines()
    if len(lines) > num_lines:
        lines = lines[-num_lines:]
    return "\n".join(lines)


def push_files(files: dict[str, str]) -> str:
    """Write YAML files to the ESPHome config directory."""
    from .config import settings
    from .paths import ContainmentError, safe_join

    results = []
    for filename, content in files.items():
        if _is_forbidden(filename):
            results.append(f"{filename}: REJECTED (secrets files cannot be pushed)")
            continue
        if not filename.endswith(".yaml"):
            results.append(f"{filename}: REJECTED (only .yaml files allowed)")
            continue
        if len(content.encode("utf-8")) > settings.max_file_bytes:
            results.append(
                f"{filename}: REJECTED (exceeds max file size "
                f"{settings.max_file_bytes} bytes)"
            )
            continue

        try:
            target = safe_join(ESPHOME_DIR, filename)
        except ContainmentError as e:
            results.append(f"{filename}: REJECTED (unsafe path: {e})")
            continue

        os.makedirs(os.path.dirname(target), exist_ok=True)
        try:
            with open(target, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            results.append(f"{filename}: OK")
        except OSError:
            results.append(f"{filename}: ERROR (write failed)")

    return "Push results:\n" + "\n".join(results)


def pull_files(filenames: list[str] | None = None) -> dict[str, str]:
    """Read YAML files from the ESPHome config directory."""
    from .paths import ContainmentError, safe_join

    result: dict[str, str] = {}
    paths: list[str] = []

    if filenames is None:
        paths = sorted(glob.glob(os.path.join(ESPHOME_DIR, "*.yaml")))
        archive_dir = os.path.join(ESPHOME_DIR, "archive")
        if os.path.isdir(archive_dir):
            paths += sorted(glob.glob(os.path.join(archive_dir, "*.yaml")))
    else:
        for fn in filenames:
            if not fn.endswith(".yaml"):
                fn = f"{fn}.yaml"
            try:
                p = safe_join(ESPHOME_DIR, fn)
            except ContainmentError:
                continue
            if os.path.isfile(p):
                paths.append(p)
                continue
            try:
                p_archive = safe_join(ESPHOME_DIR, os.path.join("archive", fn))
            except ContainmentError:
                continue
            if os.path.isfile(p_archive):
                paths.append(p_archive)

    for path in paths:
        if _is_forbidden(path):
            continue
        rel = os.path.relpath(path, ESPHOME_DIR)
        try:
            with open(path, encoding="utf-8") as f:
                result[rel] = f.read()
        except OSError:
            result[rel] = "ERROR: could not read file"

    return result


def push_fonts(files: dict[str, str]) -> str:
    from .config import settings
    from .paths import ContainmentError, safe_filename

    fonts_dir = os.path.join(ESPHOME_DIR, "fonts")
    os.makedirs(fonts_dir, exist_ok=True)

    results = []
    for filename, b64_content in files.items():
        try:
            name = safe_filename(os.path.basename(filename))
        except ContainmentError as e:
            results.append(f"{filename}: REJECTED (unsafe name: {e})")
            continue
        if name != filename:
            results.append(f"{filename}: REJECTED (path components not allowed)")
            continue
        if not _is_allowed_font(name):
            results.append(
                f"{filename}: REJECTED (extension not in "
                f"{sorted(ALLOWED_FONT_EXTENSIONS)})"
            )
            continue
        try:
            data = base64.b64decode(b64_content, validate=True)
        except Exception:
            results.append(f"{filename}: REJECTED (invalid base64)")
            continue
        if len(data) > settings.max_file_bytes:
            results.append(
                f"{filename}: REJECTED (exceeds max file size "
                f"{settings.max_file_bytes} bytes)"
            )
            continue

        target = os.path.join(fonts_dir, name)
        try:
            with open(target, "wb") as f:
                f.write(data)
            results.append(f"{filename}: OK ({len(data)} bytes)")
        except OSError:
            results.append(f"{filename}: ERROR (write failed)")

    return "Font push results:\n" + "\n".join(results)


def pull_fonts(filenames: list[str] | None = None) -> dict[str, str]:
    from .paths import ContainmentError, safe_filename

    fonts_dir = os.path.join(ESPHOME_DIR, "fonts")
    result: dict[str, str] = {}
    if not os.path.isdir(fonts_dir):
        return result

    if filenames is None:
        paths = sorted(
            p for p in glob.glob(os.path.join(fonts_dir, "*"))
            if os.path.isfile(p) and _is_allowed_font(p)
        )
    else:
        paths = []
        for fn in filenames:
            try:
                name = safe_filename(os.path.basename(fn))
            except ContainmentError:
                continue
            if not _is_allowed_font(name):
                continue
            p = os.path.join(fonts_dir, name)
            if os.path.isfile(p):
                paths.append(p)

    for path in paths:
        try:
            with open(path, "rb") as f:
                data = f.read()
            result[os.path.basename(path)] = base64.b64encode(data).decode("ascii")
        except OSError:
            result[os.path.basename(path)] = "ERROR: could not read file"

    return result
