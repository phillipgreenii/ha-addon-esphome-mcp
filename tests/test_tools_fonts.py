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


class TestFontCountCap:
    def test_cap_blocks_new_pushes(self, esphome_dir, monkeypatch):
        """Once /share/esphome/fonts/ has _FONT_COUNT_CAP files, new pushes
        are rejected. Uses a small cap via monkeypatch for fast testing."""
        import base64
        from server import tools
        monkeypatch.setattr(tools, "_FONT_COUNT_CAP", 2)

        fonts_dir = esphome_dir / "fonts"
        # Plant 2 files to hit the cap.
        (fonts_dir / "a.ttf").write_bytes(b"\x00\x01\x00\x00" + b"\x00" * 10)
        (fonts_dir / "b.ttf").write_bytes(b"\x00\x01\x00\x00" + b"\x00" * 10)

        body = base64.b64encode(b"\x00\x01\x00\x00" + b"\x00" * 10).decode()
        result = tools.push_fonts({"new.ttf": body})
        assert "REJECTED" in result
        assert "cap of 2" in result
        assert not (fonts_dir / "new.ttf").exists()

    def test_in_batch_cap_enforced(self, esphome_dir, monkeypatch):
        """In a single push batch, the cap must apply across the whole
        batch — not just the initial directory count. Plant 1 existing,
        push 3, expect the 3rd to be rejected (cap=3, 1+2=3, 3rd blocked)."""
        import base64
        from server import tools
        monkeypatch.setattr(tools, "_FONT_COUNT_CAP", 3)

        fonts_dir = esphome_dir / "fonts"
        (fonts_dir / "preexisting.ttf").write_bytes(
            b"\x00\x01\x00\x00" + b"\x00" * 10
        )

        body = base64.b64encode(b"\x00\x01\x00\x00" + b"\x00" * 10).decode()
        result = tools.push_fonts({
            "n1.ttf": body,
            "n2.ttf": body,
            "n3.ttf": body,
        })
        # Two of the three should succeed; the third hits the cap.
        assert result.count("OK") == 2
        assert result.count("REJECTED") == 1
        assert "cap of 3" in result
