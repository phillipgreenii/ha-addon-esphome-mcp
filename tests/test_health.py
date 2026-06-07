import importlib
import pytest
from httpx import AsyncClient, ASGITransport
from asgi_lifespan import LifespanManager


@pytest.fixture
async def app(monkeypatch):
    monkeypatch.setenv("ESPHOME_MCP_AUTH_TOKEN", "tok")
    import server.config, server.main
    importlib.reload(server.config)
    importlib.reload(server.main)
    async with LifespanManager(server.main.app) as manager:
        yield manager.app


class TestHealth:
    async def test_health_get_returns_200_without_auth(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/health")
            assert r.status_code == 200
            assert r.json() == {"status": "ok"}

    async def test_health_post_requires_auth(self, app):
        """Only GET is auth-whitelisted; POST /health must still be auth'd."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/health", content=b"{}")
            assert r.status_code in (401, 405)
