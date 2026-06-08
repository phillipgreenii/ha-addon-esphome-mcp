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

ESPHOME_DIR = os.environ.get("ESPHOME_DIR", "/share/esphome")
ESPHOME_BIN = "esphome"

FORBIDDEN_FILES = {"secrets.yaml", ".secret.yaml"}

ALLOWED_FONT_EXTENSIONS = {".ttf", ".otf", ".bdf", ".pcf", ".woff", ".woff2"}

_DISABLED_MSG = (
    "{action} is disabled. Enable it by setting the add-on option "
    "{option} to true (see DOCS.md for security implications). Changes "
    "take effect after the add-on is restarted."
)

_INCLUDE_TAGS = (
    "!include",
    "!include_dir_list",
    "!include_dir_named",
    "!include_dir_merge_list",
    "!include_dir_merge_named",
)


def _extract_include_path(node: "yaml.Node") -> str | None:
    """Extract the file path from an !include* node, scalar or mapping form."""
    if isinstance(node, yaml.ScalarNode):
        return node.value
    if isinstance(node, yaml.MappingNode):
        # ESPHome accepts: !include\n  file: <path>\n  vars: ...
        for k_node, v_node in node.value:
            if (
                isinstance(k_node, yaml.ScalarNode)
                and k_node.value == "file"
                and isinstance(v_node, yaml.ScalarNode)
            ):
                return v_node.value
    return None


def _scan_unsafe_includes(content: str, target_yaml_path: str) -> list[str]:
    """Return a list of unsafe !include* paths in the YAML content.

    Parses YAML with a SafeLoader that registers custom constructors for every
    ESPHome !include* tag. Each constructor validates the path. Unknown tags
    (!secret, !lambda, etc.) are ignored so the parse does not abort.

    `target_yaml_path` is where the YAML will be written; includes are
    resolved relative to its directory.
    """
    from .paths import ContainmentError, safe_join

    yaml_dir = os.path.dirname(target_yaml_path)
    unsafe: list[str] = []

    class ScanLoader(yaml.SafeLoader):
        pass

    def _check(path: str) -> None:
        if not isinstance(path, str) or not path:
            return
        if os.path.isabs(path):
            unsafe.append(path)
            return
        absolute_target = os.path.normpath(os.path.join(yaml_dir, path))
        try:
            rel_to_base = os.path.relpath(absolute_target, ESPHOME_DIR)
        except ValueError:
            unsafe.append(path)
            return
        if rel_to_base.startswith(".."):
            unsafe.append(path)
            return
        try:
            safe_join(ESPHOME_DIR, rel_to_base)
        except ContainmentError:
            unsafe.append(path)
            return
        if _is_forbidden(os.path.basename(rel_to_base)):
            unsafe.append(path)

    def _make_include_constructor():
        def constructor(loader, node):
            path = _extract_include_path(node)
            if path is not None:
                _check(path)
            return None  # don't try to actually load
        return constructor

    for tag in _INCLUDE_TAGS:
        ScanLoader.add_constructor(tag, _make_include_constructor())

    # Ignore every other custom tag rather than aborting.
    def _ignore_unknown(loader, tag_suffix, node):
        return None

    ScanLoader.add_multi_constructor("!", _ignore_unknown)

    try:
        yaml.load(content, Loader=ScanLoader)
    except yaml.YAMLError:
        # Malformed YAML — reject the push outright. ESPHome would fail to load
        # anyway, but rejecting at push time gives the client a clean error.
        unsafe.append("(malformed YAML)")

    return unsafe


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
            if _is_forbidden(path):
                continue
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


async def validate(device: str) -> str:
    """Validate an ESPHome device config. Async; semaphore-gated."""
    from .limits import get_compile_semaphore

    try:
        yaml_path = _device_yaml_path(device)
    except ValueError as e:
        return f"invalid device name (rejected by safety check): {e}"
    if not os.path.isfile(yaml_path):
        return f"Device config not found: {os.path.basename(yaml_path)}"

    sem = get_compile_semaphore()
    async with sem:
        return await asyncio.to_thread(
            _run, [ESPHOME_BIN, "config", yaml_path]
        )


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


async def logs(device: str, num_lines: int = 50) -> str:
    """Get recent logs from an ESPHome device. Async; semaphore-gated."""
    from .limits import get_compile_semaphore

    try:
        yaml_path = _device_yaml_path(device)
    except ValueError as e:
        return f"invalid device name (rejected by safety check): {e}"
    if not os.path.isfile(yaml_path):
        return f"Device config not found: {os.path.basename(yaml_path)}"

    sem = get_compile_semaphore()
    async with sem:
        output = await asyncio.to_thread(
            _run, ["timeout", "15", ESPHOME_BIN, "logs", yaml_path], 30
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

        # Reject YAML that contains !include directives pointing outside
        # ESPHOME_DIR. ESPHome's YAML loader follows these at validate/compile
        # time, so an unchecked include is an arbitrary-file read primitive.
        unsafe = _scan_unsafe_includes(content, target)
        if unsafe:
            results.append(
                f"{filename}: REJECTED (unsafe !include path(s): {unsafe})"
            )
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
