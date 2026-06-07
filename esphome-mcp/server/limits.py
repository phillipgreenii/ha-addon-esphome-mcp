"""Limits and concurrency. The real BodySizeLimitMiddleware lands in Task 12
and the real semaphore in Task 13. Stubs keep imports valid until then."""
import asyncio


def get_compile_semaphore() -> asyncio.Semaphore:
    """Stub: returns a fresh effectively-unbounded semaphore each call.
    Replaced in Task 13 with a loop-scoped cached semaphore."""
    return asyncio.Semaphore(99)
