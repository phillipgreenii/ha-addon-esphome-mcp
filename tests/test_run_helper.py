"""Tests for _run_async error paths."""
import asyncio
import pytest


class TestRunAsyncErrors:
    async def test_command_not_found(self, esphome_dir, clean_modules):
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools
        result = await tools._run_async(["/no/such/binary"])
        assert "Command not found" in result or "Command failed" in result

    async def test_non_zero_exit(self, esphome_dir, monkeypatch, clean_modules):
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools

        class FakeStream:
            def __init__(self):
                self._sent = False

            async def read(self, n=-1):
                if self._sent:
                    return b""
                self._sent = True
                return b"some error output\n"

        class FakeProc:
            def __init__(self):
                self.returncode = 7
                self.stdout = FakeStream()

            async def wait(self):
                # Let _drain see EOF first
                await asyncio.sleep(0)
                return 7

            def terminate(self): pass
            def kill(self): pass

        async def fake_create(*cmd, **kwargs):
            return FakeProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
        result = await tools._run_async(["true"])
        assert "exit 7" in result
        assert "some error output" in result

    async def test_output_capped(self, esphome_dir, monkeypatch, clean_modules):
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools

        # Generate 1 MB of output
        chunks = [b"x" * 8192 for _ in range(128)]

        class FakeStream:
            def __init__(self):
                self._chunks = iter(chunks)

            async def read(self, n=-1):
                return next(self._chunks, b"")

        class FakeProc:
            def __init__(self):
                self.returncode = 0
                self.stdout = FakeStream()

            async def wait(self):
                return 0

            def terminate(self): pass
            def kill(self): pass

        async def fake_create(*cmd, **kwargs):
            return FakeProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
        result = await tools._run_async(["echo"])
        # Upper bound: cap (64 KiB) + marker (~60 B) + small slop.
        # Lower bound: must stay CLOSE to the cap — if a regression halved
        # the cap to 32 KiB, the upper-bound check alone would miss it.
        cap = tools._RUN_OUTPUT_TAIL_BYTES
        assert len(result) <= cap + 200, (
            f"output cap exceeded: result is {len(result)} bytes, "
            f"cap is {cap}"
        )
        assert len(result) >= cap - 200, (
            f"output suspiciously below cap: {len(result)} bytes, "
            f"cap is {cap}. A regression that halved the cap would land here."
        )
        # The marker should appear at the START of the output (before the
        # tail) so the user sees it before scrolling.
        assert result.startswith(tools._RUN_TRUNCATED_MARKER_PREFIX), (
            f"truncation marker should be the leading line; got: {result[:80]!r}"
        )


class TestRunAsyncProcessGroup:
    async def test_run_async_uses_new_session(
        self, esphome_dir, monkeypatch, clean_modules
    ):
        """Verify _run_async passes start_new_session=True so kills hit
        the entire process group, not just the immediate child."""
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools

        captured_kwargs: dict = {}

        class FakeStream:
            async def read(self, n=-1):
                return b""

        class FakeProc:
            pid = 12345
            returncode = 0
            stdout = FakeStream()
            async def wait(self):
                return 0
            def send_signal(self, sig):
                pass

        async def fake_create(*cmd, **kwargs):
            captured_kwargs.update(kwargs)
            return FakeProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
        await tools._run_async(["echo", "hi"])
        assert captured_kwargs.get("start_new_session") is True

    async def test_run_async_empty_command(self, esphome_dir, clean_modules):
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools
        result = await tools._run_async([])
        assert "not found" in result.lower()


class TestRunAsyncTimeoutPath:
    async def test_timeout_returns_timed_out_message(
        self, esphome_dir, monkeypatch, clean_modules
    ):
        """Verify _run_async's timeout path: child must be SIGTERM'd via
        killpg, then SIGKILL'd if SIGTERM doesn't take within 3s."""
        import asyncio
        import signal
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools

        signal_calls: list[int] = []

        class HangingStream:
            async def read(self, n=-1):
                await asyncio.sleep(30)
                return b""

        class HangingProc:
            pid = 77777
            returncode = None
            stdout = HangingStream()
            def __init__(self):
                self._waits = 0
            async def wait(self):
                self._waits += 1
                # First wait: hang until timeout fires.
                # Subsequent waits (post-signal): return immediately so the
                # SIGTERM-grace timeout passes quickly through to SIGKILL.
                if self._waits == 1:
                    await asyncio.sleep(30)
                return 0
            def send_signal(self, sig):
                signal_calls.append(int(sig))

        async def fake_create(*cmd, **kwargs):
            return HangingProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
        monkeypatch.setattr("os.getpgid", lambda pid: pid)
        monkeypatch.setattr("os.killpg", lambda pgid, sig: signal_calls.append(int(sig)))

        result = await tools._run_async(["sleep", "30"], timeout=1)
        assert "timed out" in result.lower()
        assert int(signal.SIGTERM) in signal_calls, (
            f"expected SIGTERM on timeout; got {signal_calls}"
        )


class TestRunAsyncKillEscalation:
    async def test_sigkill_when_sigterm_does_not_take(
        self, esphome_dir, monkeypatch, clean_modules
    ):
        """The previous timeout test had a bug: HangingProc.wait() returned
        0 on the second call, so the SIGTERM-grace asyncio.wait_for never
        timed out and the SIGKILL escalation path was unreachable. This
        test fixes that by making wait() hang on both the first call
        (triggering the outer timeout) AND the second call (triggering
        the inner SIGTERM-grace timeout and the SIGKILL escalation).
        """
        import asyncio
        import signal
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools

        signal_calls: list[int] = []

        class Stream:
            async def read(self, n=-1):
                await asyncio.sleep(30)
                return b""

        class StubbornProc:
            pid = 88888
            returncode = None
            stdout = Stream()

            def __init__(self):
                self._waits = 0

            async def wait(self):
                self._waits += 1
                # Calls 1 & 2: hang. The 3s SIGTERM-grace will time out.
                # Call 3 (after SIGKILL): return immediately so the test
                # doesn't hang past its own timeout.
                if self._waits < 3:
                    await asyncio.sleep(30)
                self.returncode = -9
                return -9

            def send_signal(self, sig):
                pass

        async def fake_create(*cmd, **kwargs):
            return StubbornProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
        monkeypatch.setattr("os.getpgid", lambda pid: pid)
        monkeypatch.setattr(
            "os.killpg",
            lambda pgid, sig: signal_calls.append(int(sig)),
        )

        # Outer timeout=1 fires after 1s. SIGTERM-grace is 3s. So expect
        # ~4s elapsed before SIGKILL escalation produces the return.
        result = await tools._run_async(["sleep", "999"], timeout=1)
        assert "timed out" in result.lower()
        assert int(signal.SIGTERM) in signal_calls, (
            f"SIGTERM should be first; got {signal_calls}"
        )
        assert int(signal.SIGKILL) in signal_calls, (
            f"SIGKILL escalation must run after the 3s SIGTERM-grace; "
            f"got {signal_calls}"
        )
        # Ordering: SIGTERM must precede SIGKILL.
        assert signal_calls.index(int(signal.SIGTERM)) < signal_calls.index(
            int(signal.SIGKILL)
        )


class TestSignalProcessGroupFallback:
    async def test_killpg_failure_falls_back_to_send_signal(
        self, esphome_dir, monkeypatch, clean_modules
    ):
        """When os.killpg raises (PermissionError / ProcessLookupError),
        _signal_process_group must fall back to proc.send_signal so the
        immediate child still gets the signal. Round-2 review flagged
        this branch as untested."""
        import asyncio
        import signal
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools

        signal_calls = []

        class Stream:
            async def read(self, n=-1):
                await asyncio.sleep(30)
                return b""

        class FallbackProc:
            pid = 33333
            returncode = None
            stdout = Stream()
            def __init__(self):
                self._waits = 0
            async def wait(self):
                self._waits += 1
                if self._waits == 1:
                    await asyncio.sleep(30)  # force the outer timeout
                return 0
            def send_signal(self, sig):
                # The fallback target — what we want to verify is called.
                signal_calls.append(("send_signal", int(sig)))

        async def fake_create(*cmd, **kwargs):
            return FallbackProc()

        def fake_getpgid(pid):
            return pid

        def fake_killpg(pgid, sig):
            # Simulate "we don't own the PG" → PermissionError. This is
            # the branch _signal_process_group is supposed to catch.
            raise PermissionError("simulated: we don't own this PG")

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
        monkeypatch.setattr("os.getpgid", fake_getpgid)
        monkeypatch.setattr("os.killpg", fake_killpg)

        result = await tools._run_async(["sleep", "30"], timeout=1)
        assert "timed out" in result.lower()
        # send_signal must have been called as the fallback path.
        assert ("send_signal", int(signal.SIGTERM)) in signal_calls, (
            f"send_signal fallback was not invoked after killpg raised; "
            f"got {signal_calls}"
        )


class TestRunAsyncErrorSubclasses:
    async def test_permission_error_message(
        self, esphome_dir, monkeypatch, clean_modules
    ):
        """Round-9 split the spawn-failure message:
        FileNotFoundError → 'Command not found'
        Other OSError subclasses → 'Command failed to start'
        Verify the PermissionError branch."""
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools

        async def fake_create(*cmd, **kwargs):
            raise PermissionError(13, "Permission denied", cmd[0])

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
        result = await tools._run_async(["/some/binary"])
        assert "Command failed to start" in result, (
            f"PermissionError should yield 'Command failed to start' "
            f"(distinguishes from FileNotFoundError); got: {result!r}"
        )

    async def test_is_a_directory_error_message(
        self, esphome_dir, monkeypatch, clean_modules
    ):
        clean_modules(ESPHOME_DIR=str(esphome_dir))
        from server import tools

        async def fake_create(*cmd, **kwargs):
            raise IsADirectoryError(21, "Is a directory", cmd[0])

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
        result = await tools._run_async(["/etc"])
        assert "Command failed to start" in result
