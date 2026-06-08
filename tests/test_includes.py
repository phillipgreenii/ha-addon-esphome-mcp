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
    async def test_push_rejects_unsafe_include(self, esphome_dir, evil_include):
        from server import tools
        content = f"esphome:\n  name: x\nleak: !include {evil_include}\n"
        result = await tools.push_files({"evil.yaml": content})
        assert "REJECTED" in result
        # Make sure the file was NOT written
        assert not (esphome_dir / "evil.yaml").exists()

    async def test_push_rejects_include_dir_named_traversal(self, esphome_dir):
        from server import tools
        content = (
            "esphome:\n  name: x\n"
            "things: !include_dir_named ../../etc\n"
        )
        result = await tools.push_files({"evil.yaml": content})
        assert "REJECTED" in result

    async def test_push_rejects_include_dir_merge_list(self, esphome_dir):
        from server import tools
        content = (
            "esphome:\n  name: x\n"
            "things: !include_dir_merge_list /etc\n"
        )
        result = await tools.push_files({"evil.yaml": content})
        assert "REJECTED" in result

    async def test_push_allows_legitimate_include(self, esphome_dir):
        """An !include of a sibling file inside ESPHOME_DIR is allowed."""
        from server import tools
        content = (
            "esphome:\n  name: x\n"
            "shared: !include shared/common.yaml\n"
        )
        result = await tools.push_files({"my_device.yaml": content})
        assert "OK" in result

    async def test_push_allows_include_of_archive_neighbor(self, esphome_dir):
        from server import tools
        content = "esphome:\n  name: x\nshared: !include archive/old.yaml\n"
        result = await tools.push_files({"my_device.yaml": content})
        assert "OK" in result

    async def test_push_quoted_include_paths(self, esphome_dir):
        """Quoting the path should not bypass the check."""
        from server import tools
        content = 'esphome:\n  name: x\nleak: !include "/data/auth_token"\n'
        result = await tools.push_files({"evil.yaml": content})
        assert "REJECTED" in result


class TestIncludeScanBypasses:
    """Regression tests for verified-live exploits in the previous regex scanner."""

    async def test_quoted_path_with_escape_rejected(self, esphome_dir):
        """Round-3 review verified: this payload bypassed the regex scanner."""
        from server import tools
        payload = 'leak: !include "\\\n/data/auth_token"\n'
        result = await tools.push_files({"pwn1.yaml": payload})
        assert "REJECTED" in result
        assert not (esphome_dir / "pwn1.yaml").exists()

    async def test_mapping_form_with_absolute_file_rejected(self, esphome_dir):
        """Round-3 review verified: !include mapping form was entirely unscanned."""
        from server import tools
        payload = (
            "leak: !include\n"
            "  file: /data/auth_token\n"
            "  vars: {}\n"
        )
        result = await tools.push_files({"pwn2.yaml": payload})
        assert "REJECTED" in result
        assert not (esphome_dir / "pwn2.yaml").exists()

    async def test_mapping_form_with_traversal_file_rejected(self, esphome_dir):
        from server import tools
        payload = (
            "leak: !include\n"
            "  file: ../../etc/passwd\n"
        )
        result = await tools.push_files({"pwn3.yaml": payload})
        assert "REJECTED" in result

    async def test_unknown_custom_tag_does_not_abort_scan(self, esphome_dir):
        """ESPHome YAML uses tags like !secret, !lambda — the scanner must
        not bail out on them. A safe document containing such tags should
        still be writable."""
        from server import tools
        payload = (
            "esphome:\n"
            "  name: x\n"
            "wifi:\n"
            "  ssid: !secret wifi_ssid\n"
            "  password: !secret wifi_password\n"
            "lights:\n"
            "  - platform: binary\n"
            "    output: my_light\n"
            "    on_turn_on:\n"
            "      - lambda: !lambda 'id(my_light).turn_on();'\n"
        )
        result = await tools.push_files({"safe.yaml": payload})
        assert "OK" in result
        assert (esphome_dir / "safe.yaml").exists()

    async def test_malformed_yaml_rejected(self, esphome_dir):
        from server import tools
        # Unbalanced braces
        result = await tools.push_files({"bad.yaml": "esphome: {name: x\n"})
        assert "REJECTED" in result
        # The rejection MUST come from the malformed-YAML marker, not from
        # something else. This pins the actual failure path.
        assert "(malformed YAML)" in result

    async def test_comment_with_include_no_longer_false_positive(self, esphome_dir):
        """The old regex flagged this as unsafe; the new YAML-aware scanner
        sees it's a comment and lets it through."""
        from server import tools
        payload = (
            "# This config does not !include /etc/passwd\n"
            "esphome:\n  name: x\n"
        )
        result = await tools.push_files({"comment.yaml": payload})
        assert "OK" in result

    async def test_literal_block_with_include_text_no_longer_false_positive(self, esphome_dir):
        """!include inside a literal block scalar is text, not a directive."""
        from server import tools
        payload = (
            "esphome:\n  name: x\n"
            "description: |\n"
            "  Documentation: use !include /shared/common.yaml for shared configs.\n"
        )
        result = await tools.push_files({"lit.yaml": payload})
        assert "OK" in result

    async def test_relative_include_inside_base_still_allowed(self, esphome_dir):
        from server import tools
        payload = (
            "esphome:\n  name: x\n"
            "shared: !include sub/common.yaml\n"
        )
        result = await tools.push_files({"good.yaml": payload})
        assert "OK" in result


class TestSequenceFormBypass:
    async def test_sequence_form_absolute_rejected(self, esphome_dir):
        from server import tools
        payload = "leak: !include\n  - /etc/passwd\n  - /etc/shadow\n"
        result = await tools.push_files({"pwn4.yaml": payload})
        assert "REJECTED" in result

    async def test_mapping_without_file_key_rejected(self, esphome_dir):
        from server import tools
        # filename: instead of file: (future-ESPHome hypothetical form)
        payload = "leak: !include\n  filename: /etc/passwd\n"
        result = await tools.push_files({"pwn5.yaml": payload})
        assert "REJECTED" in result

    async def test_yaml_tag_directive_rejected(self, esphome_dir):
        from server import tools
        payload = "%TAG ! !mybang!\n---\nesphome:\n  name: x\n"
        result = await tools.push_files({"pwn6.yaml": payload})
        assert "REJECTED" in result

    async def test_yaml_yaml_directive_rejected(self, esphome_dir):
        from server import tools
        payload = "%YAML 1.2\n---\nesphome:\n  name: x\n"
        result = await tools.push_files({"pwn7.yaml": payload})
        assert "REJECTED" in result

    async def test_relative_include_inside_base_still_allowed_after_hardening(self, esphome_dir):
        """Regression: the hardening should not over-block legitimate includes."""
        from server import tools
        payload = "esphome:\n  name: x\nshared: !include sub/common.yaml\n"
        result = await tools.push_files({"good2.yaml": payload})
        assert "OK" in result


class TestTaggedScalarBypass:
    """Round-7 added _PLAIN_STR_TAGS to reject `!secret`/`!lambda`/etc
    on INNER scalars inside !include mapping/sequence forms. Without
    this hardening, an attacker could write
        leak: !include
          file: !secret real_path
    and have ESPHome resolve `!secret real_path` at validate time to
    whatever the operator stored in secrets.yaml — bypassing our
    push-time path containment check. These tests pin the hardening
    so a mutation that drops the _PLAIN_STR_TAGS check would be caught.
    """

    async def test_inner_secret_tag_in_mapping_file_rejected(self, esphome_dir):
        from server import tools
        payload = (
            "leak: !include\n"
            "  file: !secret some_secret_path\n"
        )
        result = await tools.push_files({"pwn.yaml": payload})
        assert "REJECTED" in result
        # The rejection MUST come from the _PLAIN_STR_TAGS check, not
        # from something else.
        assert "non-plain tag on !include file value" in result
        assert not (esphome_dir / "pwn.yaml").exists()

    async def test_inner_lambda_tag_in_mapping_file_rejected(self, esphome_dir):
        from server import tools
        payload = (
            "leak: !include\n"
            "  file: !lambda 'return std::string(\"x\");'\n"
        )
        result = await tools.push_files({"pwn.yaml": payload})
        assert "REJECTED" in result
        assert "non-plain tag" in result

    async def test_inner_int_tag_in_mapping_file_rejected(self, esphome_dir):
        """Even safe YAML-1.2 type tags (!!int, !!bool) get rejected
        when they appear on the include file value — those wouldn't
        normally be filename strings."""
        from server import tools
        payload = (
            "leak: !include\n"
            "  file: !!int 42\n"
        )
        result = await tools.push_files({"pwn.yaml": payload})
        assert "REJECTED" in result
        assert "non-plain tag" in result

    async def test_tagged_scalar_in_include_sequence_rejected(self, esphome_dir):
        """Same check applies to sequence-form !include entries."""
        from server import tools
        payload = (
            "leak: !include\n"
            "  - !secret bad\n"
            "  - good.yaml\n"
        )
        result = await tools.push_files({"pwn.yaml": payload})
        assert "REJECTED" in result
        # The rejection comes from the sequence's tag-check, not the
        # path-containment check, so the message should mention
        # "non-plain or non-scalar sequence entry".
        assert "non-plain" in result

    async def test_plain_str_tag_inner_value_still_accepted(self, esphome_dir):
        """!!str is in _PLAIN_STR_TAGS — explicit string tagging on
        the inner value must still pass the tag check (then go on to
        path validation, which rejects /etc/passwd as absolute)."""
        from server import tools
        payload = (
            "leak: !include\n"
            "  file: !!str /etc/passwd\n"
        )
        result = await tools.push_files({"pwn.yaml": payload})
        # Rejected, but for ABSOLUTE-PATH reasons, not tag reasons.
        assert "REJECTED" in result
        assert "non-plain tag" not in result, (
            "rejection should come from path containment, not tag check"
        )
