def test_yaml_parse_error_does_not_leak_exception_string(esphome_dir):
    from server import tools
    bad = esphome_dir / "broken.yaml"
    bad.write_text("[unterminated\nlist: !!python/object/apply:os.system\n- 'id'\n")
    info = tools._parse_device_info(str(bad))
    assert info["name"] == "error"
    # Must NOT contain the raw exception class or file paths
    assert "Traceback" not in info.get("error", "")
    assert "/" not in info.get("error", "")
