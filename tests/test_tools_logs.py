"""Tests for the logs() tool."""
import pytest


class TestLogs:
    async def test_invalid_device_name(self, esphome_dir, clean_modules):
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools
        result = await tools.logs("../escape")
        assert "invalid device" in result.lower()

    async def test_device_not_found(self, esphome_dir, clean_modules):
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools
        result = await tools.logs("nonexistent")
        assert "not found" in result.lower()

    async def test_truncates_to_num_lines(
        self, esphome_dir, monkeypatch, clean_modules
    ):
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools
        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")

        # 100 lines of output; expect logs() to keep only the last 5
        big_output = "\n".join(f"line {i}" for i in range(100)).encode() + b"\n"

        class FakeStream:
            def __init__(self, data):
                self._data = data
                self._pos = 0

            async def read(self, n=-1):
                if self._pos >= len(self._data):
                    return b""
                chunk = self._data[self._pos : self._pos + (n if n >= 0 else len(self._data))]
                self._pos += len(chunk)
                return chunk

        class FakeProc:
            def __init__(self):
                self.returncode = 0
                self.stdout = FakeStream(big_output)

            async def wait(self):
                return 0

            def terminate(self):
                pass

            def kill(self):
                pass

        async def fake_create(*cmd, **kwargs):
            return FakeProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
        result = await tools.logs("lamp", num_lines=5)
        lines = result.splitlines()
        assert len(lines) == 5
        assert lines[-1] == "line 99"


class TestLogsErrorPrefixPreservation:
    async def test_command_failed_prefix_preserved(
        self, esphome_dir, monkeypatch, clean_modules
    ):
        """When _run_async returns an error-prefixed string (e.g. "Command
        failed (exit 1): ..."), logs() must NOT strip lines off the top
        via `[-num_lines:]` — the prefix line is the most important line."""
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools

        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")

        # Fake _run_async returning a structured error message.
        async def fake_run(*args, **kwargs):
            body = "\n".join(f"trace line {i}" for i in range(100))
            return f"Command failed (exit 1):\n{body}"

        monkeypatch.setattr(tools, "_run_async", fake_run)
        result = await tools.logs("lamp", num_lines=5)
        # Error prefix must be at the START of the output.
        assert result.startswith("Command failed (exit 1):"), (
            f"error prefix lost; got: {result[:80]!r}"
        )
        # Body content must NOT be truncated either — the whole point of
        # the verbatim return path is to give the operator the full error
        # context, not a tail of a possibly-truncated body.
        assert "trace line 0" in result, (
            "error path should return verbatim — first body line missing"
        )
        assert "trace line 99" in result, (
            "error path should return verbatim — last body line missing"
        )

    async def test_truncated_marker_preserved(
        self, esphome_dir, monkeypatch, clean_modules
    ):
        """When _run_async marks the output as truncated (the leading
        '[... output truncated ...]' line), logs() must keep that marker
        even after the per-line trim."""
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools

        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")

        async def fake_run(*args, **kwargs):
            body = "\n".join(f"log line {i}" for i in range(200))
            return (
                f"[... output truncated, last {tools._RUN_OUTPUT_TAIL_BYTES} "
                f"bytes shown ...]\n{body}"
            )

        monkeypatch.setattr(tools, "_run_async", fake_run)
        result = await tools.logs("lamp", num_lines=5)
        # Marker must lead, then the last 5 lines of the body follow.
        assert result.startswith("[... output truncated"), (
            f"truncation marker dropped; got: {result[:80]!r}"
        )
        # Should contain only the LAST 5 body lines + the marker.
        body_lines = [
            ln for ln in result.splitlines()
            if not ln.startswith("[... output truncated")
        ]
        assert len(body_lines) == 5, (
            f"expected 5 body lines, got {len(body_lines)}: {body_lines!r}"
        )
        # CRITICAL: must be the LAST 5 — body was lines 0..199, we want 195..199
        assert body_lines == [f"log line {i}" for i in range(195, 200)], (
            f"expected the LAST 5 lines (195..199); got {body_lines!r}. "
            f"A regression that keeps the FIRST N lines would land here."
        )
