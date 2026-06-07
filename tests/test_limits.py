import importlib
import pytest
from httpx import AsyncClient, ASGITransport
from asgi_lifespan import LifespanManager


@pytest.fixture
async def small_app(monkeypatch):
    monkeypatch.setenv("ESPHOME_MCP_AUTH_TOKEN", "tok")
    monkeypatch.setenv("ESPHOME_MCP_MAX_BODY_BYTES", "1024")
    import server.config, server.main
    importlib.reload(server.config)
    importlib.reload(server.main)
    async with LifespanManager(server.main.app) as manager:
        yield manager.app


@pytest.fixture
async def client(small_app):
    async with AsyncClient(
        transport=ASGITransport(app=small_app), base_url="http://t"
    ) as c:
        yield c


class TestBodyLimit:
    async def test_small_body_passes(self, client):
        r = await client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer tok",
                "Content-Type": "application/json",
            },
            content=b"{}",
        )
        assert r.status_code != 413

    async def test_oversize_content_length_rejected(self, client):
        big = b"x" * 5000
        r = await client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer tok",
                "Content-Length": str(len(big)),
            },
            content=big,
        )
        assert r.status_code == 413

    async def test_chunked_without_content_length_rejected(self, client):
        # Explicit chunked encoding without Content-Length must be refused;
        # otherwise it bypasses the cap entirely.
        r = await client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer tok",
                "Transfer-Encoding": "chunked",
            },
            content=b"hello",
        )
        assert r.status_code in (411, 413)
