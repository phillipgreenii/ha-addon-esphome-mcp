import pytest


class TestSettings:
    def test_defaults(self, clean_modules):
        clean_modules(
            ESPHOME_MCP_COMPILE_ENABLED=None,
            ESPHOME_MCP_FLASH_ENABLED=None,
            ESPHOME_MCP_MAX_BODY_BYTES=None,
            ESPHOME_MCP_MAX_FILE_BYTES=None,
            ESPHOME_MCP_MAX_CONCURRENT_COMPILES=None,
        )
        from server.config import settings
        assert settings.compile_enabled is False
        assert settings.flash_enabled is False
        assert settings.max_body_bytes == 8 * 1024 * 1024
        assert settings.max_file_bytes == 1 * 1024 * 1024
        assert settings.max_concurrent_compiles == 1

    @pytest.mark.parametrize(
        "val,expected",
        [("true", True), ("True", True), ("1", True), ("yes", True), ("on", True),
         ("false", False), ("0", False), ("no", False), ("", False), ("garbage", False)],
    )
    def test_compile_enabled_parsing(self, clean_modules, val, expected):
        clean_modules(ESPHOME_MCP_COMPILE_ENABLED=val)
        from server.config import settings
        assert settings.compile_enabled is expected

    def test_numeric_parsing(self, clean_modules):
        clean_modules(
            ESPHOME_MCP_MAX_BODY_BYTES="2048",
            ESPHOME_MCP_MAX_FILE_BYTES="512",
            ESPHOME_MCP_MAX_CONCURRENT_COMPILES="3",
        )
        from server.config import settings
        assert settings.max_body_bytes == 2048
        assert settings.max_file_bytes == 512
        assert settings.max_concurrent_compiles == 3

    def test_invalid_numeric_falls_back_to_default(self, clean_modules):
        clean_modules(ESPHOME_MCP_MAX_BODY_BYTES="not-a-number")
        from server.config import settings
        assert settings.max_body_bytes == 8 * 1024 * 1024
