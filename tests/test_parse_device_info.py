def test_yaml_parse_error_does_not_leak_exception_string(esphome_dir):
    from server import tools
    bad = esphome_dir / "broken.yaml"
    bad.write_text("[unterminated\nlist: !!python/object/apply:os.system\n- 'id'\n")
    info = tools._parse_device_info(str(bad))
    assert info["name"] == "error"
    # Must NOT contain the raw exception class or file paths
    assert "Traceback" not in info.get("error", "")
    assert "/" not in info.get("error", "")


def test_secret_constructor_returns_stringified_tag(esphome_dir):
    """The custom !secret constructor in _parse_device_info returns
    "!secret <name>" as a string rather than resolving the secret.
    Exercises the constructor body that was uncovered."""
    from server import tools
    yaml_path = esphome_dir / "device.yaml"
    yaml_path.write_text(
        "esphome:\n"
        "  name: lamp\n"
        "  friendly_name: \"Lamp\"\n"
        "wifi:\n"
        "  ssid: !secret wifi_ssid\n"
        "  password: !secret wifi_password\n"
    )
    info = tools._parse_device_info(str(yaml_path))
    # Must NOT crash on !secret, and basic device info must be extracted.
    assert info["name"] == "lamp"
    assert info["friendly_name"] == "Lamp"
    assert info["file"] == "device.yaml"
    # No `error` key on success (the function only adds it on Exception).
    assert "error" not in info
