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

    async def test_chunked_transfer_encoding_rejected(self, client):
        # Transfer-Encoding: chunked must be refused regardless of CL value;
        # otherwise the body-size cap can be bypassed by streaming a chunked body.
        r = await client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer tok",
                "Transfer-Encoding": "chunked",
            },
            content=b"hello",
        )
        assert r.status_code in (411, 413)

    async def test_invalid_content_length_returns_400(self):
        """Use a direct ASGI call so httpx doesn't rewrite the
        Content-Length header. The middleware must return 400 on a
        non-integer Content-Length."""
        from server.limits import BodySizeLimitMiddleware
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        async def ok(_request):
            return JSONResponse({"ok": True})

        app = Starlette(routes=[Route("/x", ok, methods=["POST"])])
        mw = BodySizeLimitMiddleware(app, max_bytes=1024)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/x",
            "raw_path": b"/x",
            "headers": [(b"content-length", b"not-a-number")],
            "query_string": b"",
            "scheme": "http",
            "http_version": "1.1",
            "client": ("test", 0),
            "server": ("test", 80),
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        captured = []

        async def send(msg):
            captured.append(msg)

        await mw(scope, receive, send)
        assert captured[0]["status"] == 400

    async def test_missing_content_length_on_post_rejected(self):
        from server.limits import BodySizeLimitMiddleware
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        async def ok(_request):
            return JSONResponse({"ok": True})

        app = Starlette(routes=[Route("/x", ok, methods=["POST"])])
        mw = BodySizeLimitMiddleware(app, max_bytes=1024)

        # Craft an ASGI request with neither Content-Length nor Transfer-Encoding
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/x",
            "raw_path": b"/x",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "http_version": "1.1",
            "client": ("test", 0),
            "server": ("test", 80),
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        captured = []

        async def send(msg):
            captured.append(msg)

        await mw(scope, receive, send)
        # First message is http.response.start
        start = captured[0]
        assert start["status"] == 411
