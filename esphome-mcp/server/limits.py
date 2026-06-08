"""Request size limit and compile concurrency cap.

BodySizeLimitMiddleware rejects:
  - requests with no Content-Length AND a body (Transfer-Encoding: chunked etc.)
  - requests whose Content-Length exceeds the configured maximum.

get_compile_semaphore() lazily instantiates a semaphore in the CURRENT event
loop. We cache it by id(loop) so that test isolation (which reloads the loop
between tests) does not produce 'attached to a different loop' errors.
"""
import asyncio

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_bytes: int):
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        # Reject chunked / unknown-length bodies. The MCP transport always
        # sends Content-Length, so this only filters abuse.
        cl = request.headers.get("content-length")
        te = request.headers.get("transfer-encoding", "").lower()
        if "chunked" in te:
            return JSONResponse(
                {"error": "chunked transfer encoding not allowed"},
                status_code=411,
            )

        if cl is None:
            # GET/DELETE with no body — fine; pass through.
            if request.method in ("GET", "HEAD", "OPTIONS", "DELETE"):
                return await call_next(request)
            return JSONResponse(
                {"error": "Content-Length required"}, status_code=411
            )
        try:
            length = int(cl)
        except ValueError:
            return JSONResponse(
                {"error": "invalid Content-Length"}, status_code=400
            )
        if length > self.max_bytes:
            return JSONResponse(
                {"error": f"request too large (max {self.max_bytes} bytes)"},
                status_code=413,
            )
        return await call_next(request)


_SEMAPHORES: dict[int, asyncio.Semaphore] = {}


def get_compile_semaphore() -> asyncio.Semaphore:
    """Return a semaphore bound to the running event loop.

    Caching by loop-id keeps the semaphore stable within a process while
    avoiding the 'Semaphore bound to a different loop' error when tests
    spin up fresh loops between cases.
    """
    from .config import settings

    loop = asyncio.get_running_loop()
    key = id(loop)
    sem = _SEMAPHORES.get(key)
    if sem is None:
        sem = asyncio.Semaphore(settings.max_concurrent_compiles)
        _SEMAPHORES[key] = sem
    return sem


def _reset_semaphores_for_tests() -> None:
    """Test-only: drop the per-loop semaphore cache."""
    _SEMAPHORES.clear()
