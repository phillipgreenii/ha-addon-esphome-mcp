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
        """Only GET is auth-whitelisted. POST /health WITHOUT auth must be
        rejected by auth (401), not silently 405'd before auth runs — that
        would mean the route handler short-circuited auth. With a wrong
        bearer token (which CANNOT pass auth), the response must be 403:
        auth ran and rejected. Either 401 or 403 is acceptable; 405 means
        auth was bypassed."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/health",
                headers={"Authorization": "Bearer wrong-token"},
                content=b"{}",
            )
            assert r.status_code == 403, (
                f"expected auth to run for POST /health; got {r.status_code}. "
                f"405 would mean the route rejected the method before auth."
            )
