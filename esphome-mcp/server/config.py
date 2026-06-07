"""Runtime configuration loaded from environment variables.

Settings are captured at module-import time. Tests must reload this module
(via the `clean_modules` fixture) after changing env vars.
"""
import os
from dataclasses import dataclass


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"true", "1", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    compile_enabled: bool
    flash_enabled: bool
    max_body_bytes: int
    max_file_bytes: int
    max_concurrent_compiles: int


def _load() -> Settings:
    return Settings(
        compile_enabled=_bool_env("ESPHOME_MCP_COMPILE_ENABLED", False),
        flash_enabled=_bool_env("ESPHOME_MCP_FLASH_ENABLED", False),
        max_body_bytes=_int_env("ESPHOME_MCP_MAX_BODY_BYTES", 8 * 1024 * 1024),
        max_file_bytes=_int_env("ESPHOME_MCP_MAX_FILE_BYTES", 1 * 1024 * 1024),
        max_concurrent_compiles=_int_env("ESPHOME_MCP_MAX_CONCURRENT_COMPILES", 1),
    )


settings = _load()
