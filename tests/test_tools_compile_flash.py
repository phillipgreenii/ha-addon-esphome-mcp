import pytest


class TestCompileGate:
    async def test_disabled_by_default(self, esphome_dir, fake_subprocess, clean_modules):
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools
        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")
        result = await tools.compile_device("lamp")
        assert "disabled" in result.lower()
        assert fake_subprocess == []

    async def test_enabled_runs(self, esphome_dir, fake_subprocess, clean_modules):
        clean_modules(
            ESPHOME_DIR=str(esphome_dir),
            ESPHOME_MCP_COMPILE_ENABLED="true",
        )
        from server import tools
        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")
        await tools.compile_device("lamp")
        assert any("compile" in c["cmd"] for c in fake_subprocess)

    async def test_invalid_device_name(self, esphome_dir, fake_subprocess, clean_modules):
        clean_modules(
            ESPHOME_DIR=str(esphome_dir),
            ESPHOME_MCP_COMPILE_ENABLED="true",
        )
        from server import tools
        result = await tools.compile_device("../escape")
        assert "invalid device" in result.lower()
        assert fake_subprocess == []


class TestFlashGate:
    async def test_disabled_by_default(self, esphome_dir, fake_subprocess, clean_modules):
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools
        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")
        result = await tools.flash("lamp")
        assert "disabled" in result.lower()
        assert fake_subprocess == []

    async def test_enabled_runs(self, esphome_dir, fake_subprocess, clean_modules):
        clean_modules(
            ESPHOME_DIR=str(esphome_dir),
            ESPHOME_MCP_FLASH_ENABLED="true",
        )
        from server import tools
        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")
        await tools.flash("lamp")
        assert any("run" in c["cmd"] for c in fake_subprocess)


import asyncio
import importlib


class TestConcurrencyCap:
    async def test_compile_cap_one(self, esphome_dir, monkeypatch, clean_modules):
        clean_modules(
            ESPHOME_DIR=str(esphome_dir),
            ESPHOME_MCP_COMPILE_ENABLED="true",
            ESPHOME_MCP_MAX_CONCURRENT_COMPILES="1",
        )
        from server import tools
        from server.limits import _reset_semaphores_for_tests
        _reset_semaphores_for_tests()

        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")

        in_flight = 0
        peak = 0

        async def fake_run_async(cmd, timeout=120, cwd=None):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            return "ok"

        monkeypatch.setattr(tools, "_run_async", fake_run_async)

        await asyncio.gather(
            tools.compile_device("lamp"),
            tools.compile_device("lamp"),
            tools.compile_device("lamp"),
        )

        assert peak == 1, f"expected peak concurrency 1, got {peak}"

    async def test_compile_cap_two(self, esphome_dir, monkeypatch, clean_modules):
        clean_modules(
            ESPHOME_DIR=str(esphome_dir),
            ESPHOME_MCP_COMPILE_ENABLED="true",
            ESPHOME_MCP_MAX_CONCURRENT_COMPILES="2",
        )
        from server import tools
        from server.limits import _reset_semaphores_for_tests
        _reset_semaphores_for_tests()

        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")

        in_flight = 0
        peak = 0

        async def fake_run_async(cmd, timeout=120, cwd=None):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            return "ok"

        monkeypatch.setattr(tools, "_run_async", fake_run_async)

        await asyncio.gather(
            tools.compile_device("lamp"),
            tools.compile_device("lamp"),
            tools.compile_device("lamp"),
            tools.compile_device("lamp"),
        )

        assert peak == 2, f"expected peak concurrency 2, got {peak}"


class TestSubprocessCancellation:
    async def test_compile_terminates_child_on_cancel(
        self, esphome_dir, monkeypatch, clean_modules
    ):
        """Verify that cancelling the compile coroutine actually kills the
        child process group (not just drops the future). Asserts SIGTERM
        is sent via os.killpg() — i.e., the process group, not just the
        immediate child — so grandchildren (platformio, gcc) are reaped."""
        import asyncio
        import signal
        clean_modules(
            ESPHOME_DIR=str(esphome_dir),
            ESPHOME_MCP_COMPILE_ENABLED="true",
        )
        from server import tools
        from server.limits import _reset_semaphores_for_tests
        _reset_semaphores_for_tests()

        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")

        signal_calls: list[tuple[str, int]] = []

        class SlowStream:
            async def read(self, n=-1):
                await asyncio.sleep(10)  # never completes within test
                return b""

        class SlowProc:
            def __init__(self):
                self.pid = 99999  # any non-zero — patched os.killpg won't use it
                self.returncode = None
                self.stdout = SlowStream()
                self._waited = False

            async def wait(self):
                if not self._waited:
                    self._waited = True
                    await asyncio.sleep(10)
                if self.returncode is None:
                    self.returncode = 0
                return self.returncode

            def send_signal(self, sig):
                signal_calls.append(("send_signal", int(sig)))

        async def fake_create(*cmd, **kwargs):
            assert kwargs.get("start_new_session") is True, (
                "_run_async must spawn with start_new_session=True so kills "
                "hit the whole process group"
            )
            return SlowProc()

        def fake_getpgid(pid):
            return pid

        def fake_killpg(pgid, sig):
            signal_calls.append(("killpg", int(sig)))

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
        monkeypatch.setattr("os.getpgid", fake_getpgid)
        monkeypatch.setattr("os.killpg", fake_killpg)

        task = asyncio.create_task(tools.compile_device("lamp"))
        await asyncio.sleep(0.05)  # let the task start
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # SIGTERM via killpg must have been sent on cancel.
        assert ("killpg", int(signal.SIGTERM)) in signal_calls, (
            f"expected os.killpg(SIGTERM) on cancel; got {signal_calls}"
        )
