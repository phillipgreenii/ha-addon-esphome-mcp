"""ESPHome MCP tool implementations.

All tools operate locally on the Home Assistant filesystem — no SSH needed.
"""

import asyncio
import base64
import contextlib
import glob
import logging
import os
import re
import signal

import yaml

log = logging.getLogger("esphome-mcp")

ESPHOME_DIR = os.environ.get("ESPHOME_DIR", "/share/esphome")
ESPHOME_BIN = "esphome"

FORBIDDEN_FILES = {"secrets.yaml", ".secret.yaml"}

ALLOWED_FONT_EXTENSIONS = {".ttf", ".otf", ".bdf", ".pcf", ".woff", ".woff2"}

# (ext, magic_prefixes) — accept the file if its first bytes match any prefix.
FONT_MAGIC_PREFIXES: dict[str, tuple[bytes, ...]] = {
    ".ttf": (b"\x00\x01\x00\x00", b"true", b"OTTO"),  # TrueType / OpenType
    ".otf": (b"OTTO", b"\x00\x01\x00\x00"),
    ".woff": (b"wOFF",),
    ".woff2": (b"wOF2",),
    ".bdf": (b"STARTFONT",),
    ".pcf": (b"\x01fcp",),
}


def _font_magic_ok(name: str, data: bytes) -> bool:
    """Return True if data's prefix matches a known magic for name's extension."""
    name_lower = name.lower()
    for ext, prefixes in FONT_MAGIC_PREFIXES.items():
        if name_lower.endswith(ext):
            return any(data.startswith(p) for p in prefixes)
    return False


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


def _extract_include_path(node: "yaml.Node") -> str | list[str] | None:
    """Extract the file path(s) from an !include* node.

    Returns:
      str        — scalar `!include path`
      list[str]  — sequence `!include [path1, path2]`, OR a sentinel list
                   with a "(...)" string when the node form is unrecognized
                   so the conservative-reject path fires.
      None       — only when there is genuinely no path-like value at all.
    """
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
        # Mapping without a `file:` key: conservatively reject so a future
        # ESPHome syntax extension can't slip a path past the scanner.
        return ["(unrecognized !include mapping form)"]
    if isinstance(node, yaml.SequenceNode):
        # Sequence form: pull every scalar entry.
        paths: list[str] = []
        for item in node.value:
            if isinstance(item, yaml.ScalarNode):
                paths.append(item.value)
            else:
                paths.append("(non-scalar sequence entry)")
        return paths or ["(empty sequence !include)"]
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

    # %TAG / %YAML directives can rewrite tag prefixes in ways that bypass
    # `add_multi_constructor("!", ...)` — and ESPHome itself doesn't use
    # them, so reject defensively.
    if re.search(r"^\s*%(?:TAG|YAML)\b", content, re.MULTILINE):
        return ["(unsupported YAML directive)"]

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
            if path is None:
                return None
            if isinstance(path, list):
                for p in path:
                    if isinstance(p, str) and p.startswith("("):
                        # Sentinel from _extract_include_path — append
                        # directly so the push is rejected without trying
                        # to interpret the sentinel as a real path.
                        unsafe.append(p)
                    else:
                        _check(p)
            else:
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
    except (yaml.YAMLError, RecursionError):
        # Malformed YAML (or pathologically nested input that blew the
        # recursion limit) — reject the push outright.
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


_RUN_OUTPUT_TAIL_BYTES = 64 * 1024  # 64 KiB — last N bytes kept on overflow


def _signal_process_group(proc, sig: int) -> None:
    """Signal the entire process group. Falls back to the immediate child
    if the PG lookup fails (e.g., child already exited)."""
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        # Child already exited or we don't own the PG — try the child
        try:
            proc.send_signal(sig)
        except ProcessLookupError:
            pass


async def _terminate_proc(proc) -> None:
    """SIGTERM the process group; SIGKILL escalation after 3-second grace."""
    _signal_process_group(proc, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=3)
    except asyncio.TimeoutError:
        _signal_process_group(proc, signal.SIGKILL)
        await proc.wait()


async def _run_async(
    cmd: list[str], timeout: int = 120, cwd: str | None = None
) -> str:
    """Run a subprocess asynchronously and return combined stdout+stderr.

    Properties:
      - Cancellable: if the calling coroutine is cancelled, the ENTIRE
        process group (child + grandchildren like platformio/gcc) is
        terminated (SIGTERM, then SIGKILL after a 3-second grace).
      - Bounded output: at most `_RUN_OUTPUT_TAIL_BYTES` of combined output
        is retained. Earlier bytes are tail-truncated with a marker.
    """
    if not cmd:
        return "Command not found: (empty command)"

    log.info("Running: %s", " ".join(cmd))
    work_dir = cwd if cwd is not None else ESPHOME_DIR

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=work_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
    except FileNotFoundError as e:
        return f"Command not found: {e}"

    stdout = bytearray()

    # Amortized O(N) truncation: append every chunk; only collapse to the
    # tail when the buffer exceeds 2x the cap. The buffer never grows past
    # 2x the cap during streaming, and per-byte work is O(1) amortized. A
    # final trim after the read loop ensures the returned buffer respects
    # the hard cap regardless of where the stream happened to end.
    _SOFT_CAP = 2 * _RUN_OUTPUT_TAIL_BYTES
    truncated = [False]  # closure-mutable sentinel

    async def _drain():
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(8192)
            if not chunk:
                break
            stdout.extend(chunk)
            if len(stdout) > _SOFT_CAP:
                del stdout[: len(stdout) - _RUN_OUTPUT_TAIL_BYTES]
                truncated[0] = True
        # Final trim — guarantees the buffer is at most the hard cap.
        if len(stdout) > _RUN_OUTPUT_TAIL_BYTES:
            del stdout[: len(stdout) - _RUN_OUTPUT_TAIL_BYTES]
            truncated[0] = True

    drain_task = asyncio.create_task(_drain())
    wait_task = asyncio.create_task(proc.wait())

    async def _cancel_drain():
        drain_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await drain_task

    try:
        await asyncio.wait_for(
            asyncio.gather(drain_task, wait_task),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        await _terminate_proc(proc)
        await _cancel_drain()
        return f"Command timed out after {timeout}s"
    except asyncio.CancelledError:
        # Client disconnected; kill the child so it stops consuming resources.
        await _terminate_proc(proc)
        await _cancel_drain()
        raise

    output = stdout.decode("utf-8", errors="replace").strip()
    truncated_marker = (
        f"[... output truncated, last {_RUN_OUTPUT_TAIL_BYTES} bytes shown ...]\n"
        if truncated[0]
        else ""
    )

    if proc.returncode != 0:
        return f"Command failed (exit {proc.returncode}):\n{truncated_marker}{output}"
    return f"{truncated_marker}{output}" if truncated_marker else output


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
        return await _run_async([ESPHOME_BIN, "config", yaml_path])


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
        return await _run_async(
            [ESPHOME_BIN, "compile", yaml_path], timeout=300
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
        return await _run_async(
            [ESPHOME_BIN, "run", yaml_path, "--no-logs"], timeout=600
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
        # _run_async's own timeout is the only timeout we need — the
        # external `timeout` binary would fire first and produce a
        # misleading "exit 124" message.
        output = await _run_async(
            [ESPHOME_BIN, "logs", yaml_path], timeout=15
        )
    # Preserve the error/timeout prefix (and the [... truncated ...] marker)
    # by only line-trimming the body. If the helper signalled failure, return
    # the full helper output verbatim.
    error_prefixes = ("Command failed", "Command timed out", "Command not found")
    if output.startswith(error_prefixes):
        return output
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
        if not _font_magic_ok(name, data):
            results.append(
                f"{filename}: REJECTED (content does not match a known font "
                f"magic for extension)"
            )
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
