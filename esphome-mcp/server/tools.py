"""ESPHome MCP tool implementations.

All tools operate locally on the Home Assistant filesystem — no SSH needed.
"""

import asyncio
import base64
import contextlib
import glob
import logging
import os
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


# Tag URIs the ScalarNode for an !include path is allowed to carry. Anything
# else (e.g. `!secret`, `!lambda`, `!extend`) makes the value opaque from our
# scanner's perspective — ESPHome resolves the tag at validate/compile time
# to something we can't predict at push time, so we must reject.
_PLAIN_STR_TAGS = frozenset({
    "tag:yaml.org,2002:str",
    "tag:yaml.org,2002:null",  # treated as no-value
})


class _IncludeExtractResult:
    """Discriminated result from `_extract_include_path`.

    Uses object identity for the reject markers so user-supplied paths
    can NEVER be misclassified as a sentinel.
    """

    __slots__ = ("paths", "reject_reason")

    def __init__(
        self, paths: list[str] | None = None, reject_reason: str | None = None
    ) -> None:
        self.paths = paths or []
        self.reject_reason = reject_reason


def _extract_include_path(node: "yaml.Node") -> _IncludeExtractResult:
    """Extract the file path(s) from an !include* node.

    The outer node's tag is ALWAYS one of the `!include*` tags we
    registered for — PyYAML routed the construction to us because of that
    tag, so we don't inspect node.tag here. We DO inspect tags on inner
    nodes (the `file:` value in mapping form, the items of a sequence)
    because there an attacker can write e.g. `file: !secret leak_path`
    and have ESPHome resolve the path at validate-time to something we
    can't predict at push-time.

    Returns _IncludeExtractResult: a `.paths` list of user-supplied path
    strings to validate via _check, or a `.reject_reason` string the
    caller treats as unconditional-reject (which the user CAN'T forge —
    we never put user content in `.reject_reason`).
    """
    if isinstance(node, yaml.ScalarNode):
        return _IncludeExtractResult(paths=[node.value])
    if isinstance(node, yaml.MappingNode):
        # ESPHome accepts: !include\n  file: <path>\n  vars: ...
        for k_node, v_node in node.value:
            if (
                isinstance(k_node, yaml.ScalarNode)
                and k_node.value == "file"
                and isinstance(v_node, yaml.ScalarNode)
            ):
                if v_node.tag not in _PLAIN_STR_TAGS:
                    return _IncludeExtractResult(
                        reject_reason=(
                            f"non-plain tag on !include file value: "
                            f"{v_node.tag}"
                        )
                    )
                return _IncludeExtractResult(paths=[v_node.value])
        # Mapping without a `file:` key: conservatively reject so a future
        # ESPHome syntax extension can't slip a path past the scanner.
        return _IncludeExtractResult(
            reject_reason="unrecognized !include mapping form"
        )
    if isinstance(node, yaml.SequenceNode):
        if not node.value:
            return _IncludeExtractResult(
                reject_reason="empty sequence !include"
            )
        paths: list[str] = []
        for item in node.value:
            if isinstance(item, yaml.ScalarNode) and item.tag in _PLAIN_STR_TAGS:
                paths.append(item.value)
            else:
                return _IncludeExtractResult(
                    reject_reason="non-plain or non-scalar sequence entry"
                )
        return _IncludeExtractResult(paths=paths)
    return _IncludeExtractResult(
        reject_reason=f"unsupported !include node type: {type(node).__name__}"
    )


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
    # them, so reject defensively. Use the real YAML scanner (which knows
    # what a directive is) instead of a regex that false-positives on
    # text like "%TAG" appearing inside a block scalar. Directives only
    # appear at the start of a document, so break on the first non-
    # directive, non-stream-start token to bound worst-case cost.
    try:
        for token in yaml.scan(content):
            if isinstance(token, yaml.DirectiveToken):
                return [f"REJECTED: unsupported YAML directive %{token.name}"]
            if isinstance(token, (yaml.StreamStartToken, yaml.DocumentStartToken)):
                continue
            # First content token: we're past any directives. Stop.
            break
    except yaml.YAMLError:
        # If the scanner can't tokenize, the full parse below will fail
        # too — let it produce the malformed-YAML rejection.
        pass

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
            result = _extract_include_path(node)
            if result.reject_reason is not None:
                # Object-identity-based rejection: the user can't forge
                # this because reject_reason is set only in our extraction
                # function, never from user content.
                unsafe.append(f"REJECTED: {result.reject_reason}")
                return None
            for p in result.paths:
                _check(p)
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


# 64 KiB — last N bytes of subprocess output kept on overflow. Intentionally
# NOT a Settings field: this is a contract with the MCP framing layer
# (a single tool response should fit in one MCP frame), not an operator
# tunable. Bumping it should be done in code review, not via env vars.
_RUN_OUTPUT_TAIL_BYTES = 64 * 1024
# Prefixes returned by _run_async on a failure path. logs() and other
# callers use `output.startswith(_RUN_ERROR_PREFIXES)` to short-circuit
# the body-processing path so the operator sees the structured failure
# header rather than a tail of the (possibly truncated) body.
# "Command failed" matches both "Command failed (exit N): ..." and
# "Command failed to start: ...".
_RUN_ERROR_PREFIXES = ("Command failed", "Command timed out", "Command not found")
_RUN_TRUNCATED_MARKER_PREFIX = "[... output truncated"


def _signal_process_group(proc: "asyncio.subprocess.Process", sig: int) -> None:
    """Signal the entire process group. Falls back to the immediate child
    if the process-group lookup fails (e.g., child already exited).

    Sync because os.killpg / Process.send_signal don't block — safe to
    call from async code.
    """
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        # Child already exited or we don't own the PG — try the child.
        # Suppress ProcessLookupError here too (child exited between PG
        # lookup and direct signal).
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.send_signal(sig)


async def _terminate_proc(proc: "asyncio.subprocess.Process") -> None:
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
    if not cmd or not cmd[0]:
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
    except (PermissionError, IsADirectoryError, NotADirectoryError) as e:
        # Distinguish "could not start the process" from "binary missing"
        # in the operator log. Both still match _RUN_ERROR_PREFIXES via
        # the "Command failed" prefix below.
        return f"Command failed to start: {e}"

    stdout = bytearray()

    # Amortized O(N) truncation: append every chunk; only collapse to the
    # tail when the buffer exceeds 2x the cap. The buffer never grows past
    # 2x the cap during streaming, and per-byte work is O(1) amortized. A
    # final trim after the read loop ensures the returned buffer respects
    # the hard cap regardless of where the stream happened to end.
    _SOFT_CAP = 2 * _RUN_OUTPUT_TAIL_BYTES
    truncated = False

    async def _drain():
        nonlocal truncated
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(8192)
            if not chunk:
                break
            stdout.extend(chunk)
            if len(stdout) > _SOFT_CAP:
                del stdout[: len(stdout) - _RUN_OUTPUT_TAIL_BYTES]
                truncated = True
        # Final trim — guarantees the buffer is at most the hard cap.
        if len(stdout) > _RUN_OUTPUT_TAIL_BYTES:
            del stdout[: len(stdout) - _RUN_OUTPUT_TAIL_BYTES]
            truncated = True

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
        if truncated
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
    # Preserve the error/timeout prefix verbatim — the caller needs to see
    # the structured failure header, not a tail of the (possibly truncated)
    # body. Same for the "[... output truncated ...]" header: if we trim
    # lines to num_lines we'd silently drop it.
    if output.startswith(_RUN_ERROR_PREFIXES):
        return output

    truncated_marker = ""
    if output.startswith(_RUN_TRUNCATED_MARKER_PREFIX):
        marker, _, body = output.partition("\n")
        truncated_marker = marker + "\n"
        output = body

    lines = output.splitlines()
    if len(lines) > num_lines:
        lines = lines[-num_lines:]
    return truncated_marker + "\n".join(lines)


async def push_files(files: dict[str, str]) -> str:
    """Write YAML files to the ESPHome config directory.

    Async because the per-file YAML scan can take seconds on a large pushed
    file (PyYAML's pure-Python parser). Run the scan in a worker thread so
    the asyncio event loop (and `/health`) stays responsive under load.
    """
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
        # Offload the parse to a worker thread — see the function docstring.
        unsafe = await asyncio.to_thread(
            _scan_unsafe_includes, content, target
        )
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


# Defense against unbounded /share/esphome/fonts growth (an authenticated
# client could otherwise push many small valid fonts and fill the mount).
# Intentionally NOT a Settings field — 200 is well above any realistic
# ESPHome use; operators hitting this limit should clean up
# /share/esphome/fonts/ rather than tune around it.
_FONT_COUNT_CAP = 200


def push_fonts(files: dict[str, str]) -> str:
    from .config import settings
    from .paths import ContainmentError, safe_filename

    fonts_dir = os.path.join(ESPHOME_DIR, "fonts")
    os.makedirs(fonts_dir, exist_ok=True)

    existing_count = len([
        p for p in os.listdir(fonts_dir)
        if os.path.isfile(os.path.join(fonts_dir, p))
    ])

    results = []
    for filename, b64_content in files.items():
        if existing_count >= _FONT_COUNT_CAP:
            results.append(
                f"{filename}: REJECTED (font directory at cap of "
                f"{_FONT_COUNT_CAP} files; delete unused fonts from "
                f"/share/esphome/fonts/ via the Samba or SSH add-on, "
                f"then retry)"
            )
            continue
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
        is_new = not os.path.exists(target)
        try:
            with open(target, "wb") as f:
                f.write(data)
            results.append(f"{filename}: OK ({len(data)} bytes)")
            if is_new:
                existing_count += 1
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
