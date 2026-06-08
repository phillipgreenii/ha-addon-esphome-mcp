"""Tests for the pull_fonts() tool."""
import base64
import pytest


VALID_HEADER = b"\x00\x01\x00\x00\x00\x0a\x00\x80\x00\x03\x00\x20"


class TestPullFonts:
    def test_pull_all_allowed_extensions(self, esphome_dir):
        from server import tools
        fonts_dir = esphome_dir / "fonts"
        (fonts_dir / "a.ttf").write_bytes(VALID_HEADER + b"\x00" * 100)
        (fonts_dir / "b.otf").write_bytes(VALID_HEADER + b"\x00" * 50)
        result = tools.pull_fonts(None)
        assert "a.ttf" in result
        assert "b.otf" in result
        # Both should be base64 encoded
        decoded = base64.b64decode(result["a.ttf"])
        assert decoded.startswith(VALID_HEADER)

    def test_pull_skips_disallowed_extension(self, esphome_dir):
        from server import tools
        fonts_dir = esphome_dir / "fonts"
        (fonts_dir / "secret.txt").write_bytes(b"do not return me")
        result = tools.pull_fonts(None)
        assert "secret.txt" not in result

    def test_pull_explicit_filename(self, esphome_dir):
        from server import tools
        fonts_dir = esphome_dir / "fonts"
        (fonts_dir / "font.ttf").write_bytes(VALID_HEADER + b"\x00")
        result = tools.pull_fonts(["font.ttf"])
        assert "font.ttf" in result

    def test_pull_explicit_rejects_traversal(self, esphome_dir):
        from server import tools
        # Plant a sentinel outside fonts/
        outside = esphome_dir.parent / "leak.ttf"
        outside.write_bytes(b"SENSITIVE")
        result = tools.pull_fonts(["../leak.ttf"])
        assert result == {}  # silently filtered

    def test_pull_missing_fonts_dir(self, esphome_dir):
        from server import tools
        # Remove the fonts directory
        import shutil
        shutil.rmtree(esphome_dir / "fonts")
        result = tools.pull_fonts(None)
        assert result == {}
