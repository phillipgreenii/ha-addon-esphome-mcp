import pytest


class TestPushFiles:
    def test_simple_push(self, esphome_dir):
        from server import tools
        result = tools.push_files({"device.yaml": "esphome:\n  name: x\n"})
        assert "OK" in result
        assert (esphome_dir / "device.yaml").read_text().startswith("esphome:")

    def test_archive_subdir_allowed(self, esphome_dir):
        from server import tools
        result = tools.push_files({"archive/old.yaml": "x: 1\n"})
        assert "OK" in result
        assert (esphome_dir / "archive" / "old.yaml").exists()

    @pytest.mark.parametrize(
        "evil",
        [
            "../sibling.yaml",
            "../../escape.yaml",
            "foo/../../bar.yaml",
            "/absolute.yaml",
            "archive/../../sibling.yaml",
        ],
    )
    def test_traversal_rejected(self, esphome_dir, evil):
        from server import tools
        # Sentinel: nothing should be written outside esphome_dir
        sibling = esphome_dir.parent / "sibling.yaml"
        sibling_2 = esphome_dir.parent.parent / "escape.yaml"
        result = tools.push_files({evil: "x: 1\n"})
        assert "REJECTED" in result
        assert not sibling.exists()
        assert not sibling_2.exists()

    def test_secrets_rejected_direct(self, esphome_dir):
        from server import tools
        result = tools.push_files({"secrets.yaml": "wifi: pw"})
        assert "REJECTED" in result
        assert not (esphome_dir / "secrets.yaml").exists()

    def test_secrets_rejected_in_subdir(self, esphome_dir):
        from server import tools
        result = tools.push_files({"archive/secrets.yaml": "wifi: pw"})
        assert "REJECTED" in result

    def test_non_yaml_rejected(self, esphome_dir):
        from server import tools
        result = tools.push_files({"evil.py": "import os"})
        assert "REJECTED" in result

    def test_oversized_rejected(self, esphome_dir, clean_modules):
        clean_modules(
            ESPHOME_DIR=str(esphome_dir),
            ESPHOME_MCP_MAX_FILE_BYTES="100",
        )
        from server import tools
        result = tools.push_files({"big.yaml": "x" * 1000})
        assert "REJECTED" in result
