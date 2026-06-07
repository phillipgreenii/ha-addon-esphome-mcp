import pytest


class TestDeviceResolution:
    def test_simple(self, esphome_dir):
        from server import tools
        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")
        assert tools._device_yaml_path("lamp") == str(esphome_dir / "lamp.yaml")

    def test_archive(self, esphome_dir):
        from server import tools
        (esphome_dir / "archive" / "old.yaml").write_text("x: 1")
        assert tools._device_yaml_path("old") == str(esphome_dir / "archive" / "old.yaml")

    @pytest.mark.parametrize(
        "evil",
        ["../configuration", "../../etc/passwd", "/data/auth_token", "a/b", "..", "."],
    )
    def test_traversal_raises(self, esphome_dir, evil):
        from server import tools
        with pytest.raises(ValueError):
            tools._device_yaml_path(evil)

    def test_validate_handles_invalid_name_cleanly(self, esphome_dir):
        from server import tools
        out = tools.validate("../etc/passwd")
        assert "invalid device" in out.lower()
