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
