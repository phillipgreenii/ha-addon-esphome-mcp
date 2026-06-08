"""Tests for the logs() tool."""
import pytest


class TestLogs:
    async def test_invalid_device_name(self, esphome_dir, clean_modules):
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools
        result = await tools.logs("../escape")
        assert "invalid device" in result.lower()

    async def test_device_not_found(self, esphome_dir, clean_modules):
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools
        result = await tools.logs("nonexistent")
        assert "not found" in result.lower()

    async def test_truncates_to_num_lines(
        self, esphome_dir, monkeypatch, clean_modules
    ):
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools
        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")

        # 100 lines of output; expect logs() to keep only the last 5
        big_output = "\n".join(f"line {i}" for i in range(100)).encode() + b"\n"

        class FakeStream:
            def __init__(self, data):
                self._data = data
                self._pos = 0

            async def read(self, n=-1):
                if self._pos >= len(self._data):
                    return b""
                chunk = self._data[self._pos : self._pos + (n if n >= 0 else len(self._data))]
                self._pos += len(chunk)
                return chunk

        class FakeProc:
            def __init__(self):
                self.returncode = 0
                self.stdout = FakeStream(big_output)

            async def wait(self):
                return 0

            def terminate(self):
                pass

            def kill(self):
                pass

        async def fake_create(*cmd, **kwargs):
            return FakeProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
        result = await tools.logs("lamp", num_lines=5)
        lines = result.splitlines()
        assert len(lines) == 5
        assert lines[-1] == "line 99"
