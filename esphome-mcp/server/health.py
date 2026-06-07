"""Liveness endpoint for Supervisor watchdog."""
from starlette.responses import JSONResponse
from starlette.routing import Route


async def healthcheck(_request):
    return JSONResponse({"status": "ok"})


health_route = Route("/health", healthcheck, methods=["GET"])
