"""Bearer token authentication middleware for the MCP server."""
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Fail-closed bearer auth. The token is read from the environment on
    each request, so rotation does not require a process restart for the
    middleware itself (the auth_token add-on option still requires restart
    because that's how Supervisor passes options into the env)."""

    HEALTH_PATH = "/health"

    async def dispatch(self, request: Request, call_next):
        if request.url.path == self.HEALTH_PATH and request.method == "GET":
            return await call_next(request)

        expected_token = os.environ.get("ESPHOME_MCP_AUTH_TOKEN", "")
        if not expected_token:
            return JSONResponse(
                {"error": "Server misconfigured: no auth token set"},
                status_code=503,
            )

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                {"error": "Missing or invalid Authorization header"},
                status_code=401,
            )

        token = auth_header[len("Bearer "):]
        if not secrets.compare_digest(token, expected_token):
            return JSONResponse({"error": "Invalid token"}, status_code=403)

        return await call_next(request)
