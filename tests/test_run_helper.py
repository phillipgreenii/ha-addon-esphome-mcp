"""Tests for _run_async error paths."""
import asyncio
import pytest


class TestRunAsyncErrors:
    async def test_command_not_found(self, esphome_dir, clean_modules):
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools
        result = await tools._run_async(["/no/such/binary"])
        assert "Command not found" in result or "Command failed" in result

    async def test_non_zero_exit(self, esphome_dir, monkeypatch, clean_modules):
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools

        class FakeStream:
            def __init__(self):
                self._sent = False

            async def read(self, n=-1):
                if self._sent:
                    return b""
                self._sent = True
                return b"some error output\n"

        class FakeProc:
            def __init__(self):
                self.returncode = 7
                self.stdout = FakeStream()

            async def wait(self):
                # Let _drain see EOF first
                await asyncio.sleep(0)
                return 7

            def terminate(self): pass
            def kill(self): pass

        async def fake_create(*cmd, **kwargs):
            return FakeProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
        result = await tools._run_async(["true"])
        assert "exit 7" in result
        assert "some error output" in result

    async def test_output_capped(self, esphome_dir, monkeypatch, clean_modules):
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools

        # Generate 1 MB of output
        chunks = [b"x" * 8192 for _ in range(128)]

        class FakeStream:
            def __init__(self):
                self._chunks = iter(chunks)

            async def read(self, n=-1):
                return next(self._chunks, b"")

        class FakeProc:
            def __init__(self):
                self.returncode = 0
                self.stdout = FakeStream()

            async def wait(self):
                return 0

            def terminate(self): pass
            def kill(self): pass

        async def fake_create(*cmd, **kwargs):
            return FakeProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
        result = await tools._run_async(["echo"])
        # Output cap is 64 KiB; result must be smaller than 1 MB.
        assert len(result) < 200_000
        assert "truncated" in result
