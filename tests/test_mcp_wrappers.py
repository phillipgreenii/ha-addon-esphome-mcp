"""End-to-end test verifying the FastMCP tool registration actually wires
through to the tool implementation."""
import importlib
import pytest
from httpx import AsyncClient, ASGITransport
from asgi_lifespan import LifespanManager


@pytest.fixture
async def mcp_app(esphome_dir, monkeypatch):
    monkeypatch.setenv("ESPHOME_MCP_AUTH_TOKEN", "tok")
    monkeypatch.setenv("ESPHOME_DIR", str(esphome_dir))
    import server.config, server.main
    importlib.reload(server.config)
    importlib.reload(server.main)
    async with LifespanManager(server.main.app) as manager:
        yield manager.app


_MCP_HEADERS = {
    "Authorization": "Bearer tok",
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


class TestMcpToolWiring:
    """End-to-end checks that the FastMCP @mcp.tool() wrappers are correctly
    wired to the implementations in `server.tools`. If a wrapper body had a
    typo (e.g. `tools.flsh(device)`) these tests would catch it."""

    async def test_tools_list_includes_all_nine(self, mcp_app):
        async with AsyncClient(
            transport=ASGITransport(app=mcp_app),
            base_url="http://localhost:8099",
        ) as c:
            # MCP "tools/list" request via JSON-RPC over streamable HTTP
            r = await c.post(
                "/mcp",
                headers=_MCP_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/list",
                    "params": {},
                },
            )
            # If the route works at all, status should be 200 or an SSE start.
            # We don't parse the full streamed response — just confirm we got
            # past auth and the route handler.
            assert r.status_code in (200, 202)
            text = r.text
            # All nine tool names should appear somewhere in the response body.
            for name in (
                "esphome_list_devices",
                "esphome_validate",
                "esphome_compile",
                "esphome_flash",
                "esphome_logs",
                "esphome_push_files",
                "esphome_pull_files",
                "esphome_push_fonts",
                "esphome_pull_fonts",
            ):
                assert name in text, f"missing tool registration: {name}"

    async def test_call_list_devices_exercises_wrapper(self, mcp_app):
        """tools/call for esphome_list_devices should run the wrapper body
        and return the list_devices() output. Catches typos like
        `tools.flsh(...)` that would only surface at call time."""
        async with AsyncClient(
            transport=ASGITransport(app=mcp_app),
            base_url="http://localhost:8099",
        ) as c:
            r = await c.post(
                "/mcp",
                headers=_MCP_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "esphome_list_devices",
                        "arguments": {},
                    },
                },
            )
            assert r.status_code in (200, 202)
            # esphome_dir is empty → list_devices() returns this exact string.
            assert "No device configurations found." in r.text

    async def test_call_compile_disabled_exercises_wrapper(self, mcp_app):
        """compile is opt-in; default settings disable it. The wrapper body
        forwards to tools.compile_device which returns the disabled message."""
        async with AsyncClient(
            transport=ASGITransport(app=mcp_app),
            base_url="http://localhost:8099",
        ) as c:
            r = await c.post(
                "/mcp",
                headers=_MCP_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "esphome_compile",
                        "arguments": {"device": "anything"},
                    },
                },
            )
            assert r.status_code in (200, 202)
            assert "compile is disabled" in r.text

    async def test_call_validate_wrapper(self, mcp_app):
        """validate wrapper: with no devices, expect 'invalid device name'
        or 'not found' from the underlying tool."""
        async with AsyncClient(
            transport=ASGITransport(app=mcp_app),
            base_url="http://localhost:8099",
        ) as c:
            r = await c.post(
                "/mcp",
                headers=_MCP_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 10,
                    "method": "tools/call",
                    "params": {
                        "name": "esphome_validate",
                        "arguments": {"device": "nonexistent"},
                    },
                },
            )
            assert r.status_code in (200, 202)
            assert "not found" in r.text.lower()

    async def test_call_flash_disabled_wrapper(self, mcp_app):
        """flash is opt-in; default settings disable it."""
        async with AsyncClient(
            transport=ASGITransport(app=mcp_app),
            base_url="http://localhost:8099",
        ) as c:
            r = await c.post(
                "/mcp",
                headers=_MCP_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "tools/call",
                    "params": {
                        "name": "esphome_flash",
                        "arguments": {"device": "anything"},
                    },
                },
            )
            assert r.status_code in (200, 202)
            assert "flash is disabled" in r.text.lower()

    async def test_call_logs_wrapper(self, mcp_app):
        """logs wrapper: with no devices, expect 'not found'."""
        async with AsyncClient(
            transport=ASGITransport(app=mcp_app),
            base_url="http://localhost:8099",
        ) as c:
            r = await c.post(
                "/mcp",
                headers=_MCP_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 12,
                    "method": "tools/call",
                    "params": {
                        "name": "esphome_logs",
                        "arguments": {"device": "nonexistent"},
                    },
                },
            )
            assert r.status_code in (200, 202)
            assert "not found" in r.text.lower()

    async def test_call_push_files_wrapper(self, mcp_app, esphome_dir):
        """push_files wrapper: should write a yaml file and return 'OK'."""
        async with AsyncClient(
            transport=ASGITransport(app=mcp_app),
            base_url="http://localhost:8099",
        ) as c:
            r = await c.post(
                "/mcp",
                headers=_MCP_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 13,
                    "method": "tools/call",
                    "params": {
                        "name": "esphome_push_files",
                        "arguments": {
                            "files": {"x.yaml": "esphome:\n  name: x\n"},
                        },
                    },
                },
            )
            assert r.status_code in (200, 202)
            assert "OK" in r.text
            assert (esphome_dir / "x.yaml").exists()

    async def test_call_pull_files_wrapper(self, mcp_app, esphome_dir):
        """pull_files wrapper: should return the contents of an existing yaml."""
        (esphome_dir / "lamp.yaml").write_text("esphome:\n  name: lamp\n")
        async with AsyncClient(
            transport=ASGITransport(app=mcp_app),
            base_url="http://localhost:8099",
        ) as c:
            r = await c.post(
                "/mcp",
                headers=_MCP_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 14,
                    "method": "tools/call",
                    "params": {
                        "name": "esphome_pull_files",
                        "arguments": {"filenames": ["lamp.yaml"]},
                    },
                },
            )
            assert r.status_code in (200, 202)
            assert "lamp.yaml" in r.text

    async def test_call_push_fonts_wrapper(self, mcp_app, esphome_dir):
        """push_fonts wrapper: should accept a valid TTF base64 payload."""
        import base64
        ttf_payload = base64.b64encode(
            b"\x00\x01\x00\x00" + b"\x00" * 100
        ).decode()
        async with AsyncClient(
            transport=ASGITransport(app=mcp_app),
            base_url="http://localhost:8099",
        ) as c:
            r = await c.post(
                "/mcp",
                headers=_MCP_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 15,
                    "method": "tools/call",
                    "params": {
                        "name": "esphome_push_fonts",
                        "arguments": {"files": {"font.ttf": ttf_payload}},
                    },
                },
            )
            assert r.status_code in (200, 202)
            assert "OK" in r.text
            assert (esphome_dir / "fonts" / "font.ttf").exists()

    async def test_call_pull_fonts_wrapper(self, mcp_app, esphome_dir):
        """pull_fonts wrapper: should return base64 content of an existing font."""
        (esphome_dir / "fonts" / "f.ttf").write_bytes(b"\x00\x01\x00\x00" + b"\x00" * 100)
        async with AsyncClient(
            transport=ASGITransport(app=mcp_app),
            base_url="http://localhost:8099",
        ) as c:
            r = await c.post(
                "/mcp",
                headers=_MCP_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 16,
                    "method": "tools/call",
                    "params": {
                        "name": "esphome_pull_fonts",
                        "arguments": {"filenames": ["f.ttf"]},
                    },
                },
            )
            assert r.status_code in (200, 202)
            assert "f.ttf" in r.text


class TestIngressRealisticHost:
    """Regression: round-2 silently broke ingress requests because the MCP
    SDK auto-enables DNS-rebinding protection when host is loopback. Under
    HA Supervisor ingress, the upstream Host header is the addon container
    name, NOT loopback. Make sure non-loopback Host headers succeed."""

    async def test_addon_container_host_succeeds(self, mcp_app):
        async with AsyncClient(
            transport=ASGITransport(app=mcp_app),
            base_url="http://addon_local_esphome_mcp:8099",
        ) as c:
            r = await c.post(
                "/mcp",
                headers=_MCP_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 100,
                    "method": "tools/list",
                    "params": {},
                },
            )
            assert r.status_code == 200, (
                f"non-loopback Host should pass; got {r.status_code}. "
                f"Round-2 broke this by removing FastMCP host kwarg."
            )

    async def test_external_ha_host_succeeds(self, mcp_app):
        async with AsyncClient(
            transport=ASGITransport(app=mcp_app),
            base_url="http://homeassistant.local:8123",
        ) as c:
            r = await c.post(
                "/mcp",
                headers=_MCP_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 101,
                    "method": "tools/list",
                    "params": {},
                },
            )
            assert r.status_code == 200, (
                f"HA-external Host should pass; got {r.status_code}"
            )
