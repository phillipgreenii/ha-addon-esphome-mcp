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

    async def test_validate_handles_invalid_name_cleanly(self, esphome_dir):
        from server import tools
        out = await tools.validate("../etc/passwd")
        assert "invalid device" in out.lower()


class TestListDevices:
    def test_archive_secrets_filtered(self, esphome_dir):
        from server import tools
        # Make legitimate active + archived configs
        (esphome_dir / "active.yaml").write_text("esphome:\n  name: active\n")
        (esphome_dir / "archive" / "old.yaml").write_text(
            "esphome:\n  name: old\n"
        )
        # Plant a secrets.yaml in archive
        (esphome_dir / "archive" / "secrets.yaml").write_text(
            "wifi_password: super-secret\n"
        )
        result = tools.list_devices()
        assert "active" in result
        assert "old" in result
        assert "secrets" not in result
        assert "super-secret" not in result

    def test_empty_directory(self, esphome_dir):
        from server import tools
        # Clear out the default subdirs by NOT planting any .yaml files
        result = tools.list_devices()
        assert "No device configurations found" in result
