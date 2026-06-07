"""Regression tests for the !include exfiltration fix."""
import pytest


class TestIncludeRejection:
    @pytest.mark.parametrize(
        "evil_include",
        [
            "/data/auth_token",
            "/etc/passwd",
            "/share/esphome/secrets.yaml",  # forbidden by name
            "../secrets.yaml",
            "../../etc/passwd",
            "foo/../../etc/shadow",
            "/share/configuration.yaml",
        ],
    )
    def test_push_rejects_unsafe_include(self, esphome_dir, evil_include):
        from server import tools
        content = f"esphome:\n  name: x\nleak: !include {evil_include}\n"
        result = tools.push_files({"evil.yaml": content})
        assert "REJECTED" in result
        # Make sure the file was NOT written
        assert not (esphome_dir / "evil.yaml").exists()

    def test_push_rejects_include_dir_named_traversal(self, esphome_dir):
        from server import tools
        content = (
            "esphome:\n  name: x\n"
            "things: !include_dir_named ../../etc\n"
        )
        result = tools.push_files({"evil.yaml": content})
        assert "REJECTED" in result

    def test_push_rejects_include_dir_merge_list(self, esphome_dir):
        from server import tools
        content = (
            "esphome:\n  name: x\n"
            "things: !include_dir_merge_list /etc\n"
        )
        result = tools.push_files({"evil.yaml": content})
        assert "REJECTED" in result

    def test_push_allows_legitimate_include(self, esphome_dir):
        """An !include of a sibling file inside ESPHOME_DIR is allowed."""
        from server import tools
        content = (
            "esphome:\n  name: x\n"
            "shared: !include shared/common.yaml\n"
        )
        result = tools.push_files({"my_device.yaml": content})
        assert "OK" in result

    def test_push_allows_include_of_archive_neighbor(self, esphome_dir):
        from server import tools
        content = "esphome:\n  name: x\nshared: !include archive/old.yaml\n"
        result = tools.push_files({"my_device.yaml": content})
        assert "OK" in result

    def test_push_quoted_include_paths(self, esphome_dir):
        """Quoting the path should not bypass the check."""
        from server import tools
        content = 'esphome:\n  name: x\nleak: !include "/data/auth_token"\n'
        result = tools.push_files({"evil.yaml": content})
        assert "REJECTED" in result
