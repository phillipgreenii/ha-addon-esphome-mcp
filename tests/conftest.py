"""Shared pytest fixtures.

Reload contract for tests that change env vars:
  1. monkeypatch.setenv(...)
  2. importlib.reload(server.config)   — refreshes settings dataclass
  3. importlib.reload(server.limits)   — drops the cached semaphore
  4. importlib.reload(server.tools)    — captures fresh ESPHOME_DIR
  5. (optional) importlib.reload(server.main) — rebuilds ASGI app

The `clean_modules` fixture below does this for you.
"""
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "esphome-mcp"))


@pytest.fixture
def clean_modules(monkeypatch):
    """Provide a `reload(**env)` callable that fully refreshes settings."""
    def reload(**env):
        for k, v in env.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, v)
        for name in ("server.config", "server.limits", "server.tools", "server.main"):
            if name in sys.modules:
                importlib.reload(sys.modules[name])
        # Force re-import to capture any not-yet-loaded modules
        import server.config  # noqa: F401
        import server.tools  # noqa: F401
        return sys.modules
    return reload


@pytest.fixture
def esphome_dir(tmp_path, monkeypatch, clean_modules):
    """Isolated ESPHOME_DIR equivalent (mirrors /share/esphome)."""
    d = tmp_path / "esphome"
    d.mkdir()
    (d / "archive").mkdir()
    (d / "fonts").mkdir()
    clean_modules(ESPHOME_DIR=str(d))
    return d


@pytest.fixture
def fake_subprocess(monkeypatch):
    """Patch asyncio.create_subprocess_exec to record calls.

    The sync `subprocess.run` patch was here historically when tools.py
    had a sync `_run` helper; that helper was removed and nothing in the
    server-side code path calls `subprocess.run` anymore.
    """
    calls = []

    class FakeStream:
        def __init__(self, data: bytes = b"ok\n"):
            self._data = data
            self._pos = 0

        async def read(self, n=-1):
            if self._pos >= len(self._data):
                return b""
            if n < 0:
                chunk = self._data[self._pos:]
                self._pos = len(self._data)
            else:
                chunk = self._data[self._pos : self._pos + n]
                self._pos += len(chunk)
            return chunk

    class FakeProc:
        def __init__(self, cmd):
            self.returncode = 0
            self.stdout = FakeStream()
            self._cmd = cmd

        async def wait(self):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    async def fake_create(*cmd, **kwargs):
        calls.append({"cmd": list(cmd), "kwargs": kwargs})
        return FakeProc(cmd)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
    return calls
