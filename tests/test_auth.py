import importlib
import pytest
from asgi_lifespan import LifespanManager
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def app_with_token(monkeypatch):
    monkeypatch.setenv("ESPHOME_MCP_AUTH_TOKEN", "good-token")
    import server.config, server.main
    importlib.reload(server.config)
    importlib.reload(server.main)
    async with LifespanManager(server.main.app) as manager:
        yield manager.app


@pytest.fixture
async def client(app_with_token):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_token), base_url="http://t"
    ) as c:
        yield c


class TestAuth:
    async def test_no_token_returns_401(self, client):
        r = await client.post("/mcp", content=b"{}")
        assert r.status_code == 401

    async def test_wrong_token_returns_403(self, client):
        r = await client.post(
            "/mcp",
            headers={"Authorization": "Bearer wrong"},
            content=b"{}",
        )
        assert r.status_code == 403

    async def test_right_token_proceeds(self, client):
        r = await client.post(
            "/mcp",
            headers={"Authorization": "Bearer good-token"},
            content=b"{}",
        )
        assert r.status_code not in (401, 403)

    async def test_token_read_at_request_time(self, client, monkeypatch):
        # Rotate the env var in place; no reload of server.main
        monkeypatch.setenv("ESPHOME_MCP_AUTH_TOKEN", "rotated")
        r = await client.post(
            "/mcp",
            headers={"Authorization": "Bearer rotated"},
            content=b"{}",
        )
        assert r.status_code not in (401, 403)
        # Old token should now fail
        r2 = await client.post(
            "/mcp",
            headers={"Authorization": "Bearer good-token"},
            content=b"{}",
        )
        assert r2.status_code == 403
        # monkeypatch tears down automatically; no manual finally needed


class TestFailClosed:
    async def test_empty_env_token_rejects(self, monkeypatch):
        monkeypatch.setenv("ESPHOME_MCP_AUTH_TOKEN", "")
        import server.config, server.main
        importlib.reload(server.config)
        importlib.reload(server.main)
        async with LifespanManager(server.main.app) as manager:
            async with AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://t"
            ) as c:
                r = await c.post(
                    "/mcp",
                    headers={"Authorization": "Bearer anything"},
                    content=b"{}",
                )
                assert r.status_code == 503
