import pytest


class TestCompileGate:
    async def test_disabled_by_default(self, esphome_dir, fake_subprocess, clean_modules):
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools
        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")
        result = await tools.compile_device("lamp")
        assert "disabled" in result.lower()
        assert fake_subprocess == []

    async def test_enabled_runs(self, esphome_dir, fake_subprocess, clean_modules):
        clean_modules(
            ESPHOME_DIR=str(esphome_dir),
            ESPHOME_MCP_COMPILE_ENABLED="true",
        )
        from server import tools
        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")
        await tools.compile_device("lamp")
        assert any("compile" in c["cmd"] for c in fake_subprocess)

    async def test_invalid_device_name(self, esphome_dir, fake_subprocess, clean_modules):
        clean_modules(
            ESPHOME_DIR=str(esphome_dir),
            ESPHOME_MCP_COMPILE_ENABLED="true",
        )
        from server import tools
        result = await tools.compile_device("../escape")
        assert "invalid device" in result.lower()
        assert fake_subprocess == []


class TestFlashGate:
    async def test_disabled_by_default(self, esphome_dir, fake_subprocess, clean_modules):
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools
        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")
        result = await tools.flash("lamp")
        assert "disabled" in result.lower()
        assert fake_subprocess == []

    async def test_enabled_runs(self, esphome_dir, fake_subprocess, clean_modules):
        clean_modules(
            ESPHOME_DIR=str(esphome_dir),
            ESPHOME_MCP_FLASH_ENABLED="true",
        )
        from server import tools
        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")
        await tools.flash("lamp")
        assert any("run" in c["cmd"] for c in fake_subprocess)


import asyncio
import importlib


class TestConcurrencyCap:
    async def test_compile_cap_one(self, esphome_dir, monkeypatch, clean_modules):
        clean_modules(
            ESPHOME_DIR=str(esphome_dir),
            ESPHOME_MCP_COMPILE_ENABLED="true",
            ESPHOME_MCP_MAX_CONCURRENT_COMPILES="1",
        )
        from server import tools
        from server.limits import _reset_semaphores_for_tests
        _reset_semaphores_for_tests()

        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")

        in_flight = 0
        peak = 0

        async def fake_to_thread(func, *args, **kwargs):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            return "ok"

        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        await asyncio.gather(
            tools.compile_device("lamp"),
            tools.compile_device("lamp"),
            tools.compile_device("lamp"),
        )

        assert peak == 1, f"expected peak concurrency 1, got {peak}"

    async def test_compile_cap_two(self, esphome_dir, monkeypatch, clean_modules):
        clean_modules(
            ESPHOME_DIR=str(esphome_dir),
            ESPHOME_MCP_COMPILE_ENABLED="true",
            ESPHOME_MCP_MAX_CONCURRENT_COMPILES="2",
        )
        from server import tools
        from server.limits import _reset_semaphores_for_tests
        _reset_semaphores_for_tests()

        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")

        in_flight = 0
        peak = 0

        async def fake_to_thread(func, *args, **kwargs):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            return "ok"

        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        await asyncio.gather(
            tools.compile_device("lamp"),
            tools.compile_device("lamp"),
            tools.compile_device("lamp"),
            tools.compile_device("lamp"),
        )

        assert peak == 2, f"expected peak concurrency 2, got {peak}"
