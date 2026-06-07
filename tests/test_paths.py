"""Tests for filesystem containment helpers."""
import os
import pytest

from server.paths import (
    ContainmentError,
    safe_filename,
    safe_join,
)


class TestSafeJoin:
    def test_simple_name(self, tmp_path):
        assert safe_join(str(tmp_path), "device.yaml") == str(tmp_path / "device.yaml")

    def test_subdir_allowed(self, tmp_path):
        assert (
            safe_join(str(tmp_path), "archive/device.yaml")
            == str(tmp_path / "archive" / "device.yaml")
        )

    @pytest.mark.parametrize(
        "evil",
        [
            "../configuration.yaml",
            "../../etc/passwd",
            "foo/../../../secrets.yaml",
            "archive/../../configuration.yaml",
            "/absolute/path.yaml",
            "/etc/passwd",
            "./../escape.yaml",
            "subdir/./../../escape.yaml",
        ],
    )
    def test_traversal_rejected(self, tmp_path, evil):
        with pytest.raises(ContainmentError):
            safe_join(str(tmp_path), evil)

    def test_null_byte_rejected(self, tmp_path):
        with pytest.raises(ContainmentError):
            safe_join(str(tmp_path), "device\x00.yaml")

    def test_empty_rejected(self, tmp_path):
        with pytest.raises(ContainmentError):
            safe_join(str(tmp_path), "")

    def test_symlink_leaf_escape_rejected(self, tmp_path):
        outside = tmp_path.parent / "outside.yaml"
        outside.write_text("x")
        (tmp_path / "link.yaml").symlink_to(outside)
        with pytest.raises(ContainmentError):
            safe_join(str(tmp_path), "link.yaml")

    def test_symlink_parent_escape_rejected(self, tmp_path):
        """A symlinked PARENT directory pointing outside base must be caught
        even if the leaf file does not yet exist (matters for writes)."""
        outside_dir = tmp_path.parent / "outside_dir"
        outside_dir.mkdir()
        (tmp_path / "subdir").symlink_to(outside_dir)
        with pytest.raises(ContainmentError):
            safe_join(str(tmp_path), "subdir/newfile.yaml")


class TestSafeFilename:
    def test_simple(self):
        assert safe_filename("device.yaml") == "device.yaml"

    @pytest.mark.parametrize(
        "evil",
        ["../x.yaml", "/etc/passwd", "a/b.yaml", "x\x00.yaml", "", ".", ".."],
    )
    def test_rejected(self, evil):
        with pytest.raises(ContainmentError):
            safe_filename(evil)
