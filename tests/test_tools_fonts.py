import base64
import pytest


VALID_HEADER = b"\x00\x01\x00\x00\x00\x0a\x00\x80\x00\x03\x00\x20"


class TestPushFonts:
    def test_simple_ttf(self, esphome_dir):
        from server import tools
        body = base64.b64encode(VALID_HEADER + b"\x00" * 1000).decode()
        result = tools.push_fonts({"font.ttf": body})
        assert "OK" in result
        assert (esphome_dir / "fonts" / "font.ttf").exists()

    @pytest.mark.parametrize(
        "bad_name",
        ["evil.py", "evil.sh", "evil.bin", "no_extension", "evil.ttf.py"],
    )
    def test_extension_rejected(self, esphome_dir, bad_name):
        from server import tools
        body = base64.b64encode(b"x" * 10).decode()
        result = tools.push_fonts({bad_name: body})
        assert "REJECTED" in result

    def test_path_components_rejected(self, esphome_dir):
        from server import tools
        body = base64.b64encode(VALID_HEADER + b"\x00" * 100).decode()
        result = tools.push_fonts({"../escape.ttf": body})
        assert not (esphome_dir.parent / "escape.ttf").exists()
        assert "REJECTED" in result

    def test_oversized_rejected(self, esphome_dir, clean_modules):
        clean_modules(
            ESPHOME_DIR=str(esphome_dir),
            ESPHOME_MCP_MAX_FILE_BYTES="1024",
        )
        from server import tools
        # Valid TTF magic prefix so we hit the size check, not the magic check.
        body = base64.b64encode(VALID_HEADER + b"\x00" * 5000).decode()
        result = tools.push_fonts({"big.ttf": body})
        assert "REJECTED" in result
        assert "max file size" in result

    def test_invalid_base64_rejected(self, esphome_dir):
        from server import tools
        result = tools.push_fonts({"x.ttf": "@@@not-base64@@@"})
        assert "REJECTED" in result

    def test_garbage_payload_rejected(self, esphome_dir):
        import base64
        from server import tools
        # 1000 bytes of garbage that doesn't match any font magic
        body = base64.b64encode(b"\xff" * 1000).decode()
        result = tools.push_fonts({"garbage.ttf": body})
        assert "REJECTED" in result
        assert "magic" in result.lower()
