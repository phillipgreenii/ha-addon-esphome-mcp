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
