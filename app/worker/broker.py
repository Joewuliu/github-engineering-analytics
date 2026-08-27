import asyncio
import sys

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import AsyncIO

from app.config import get_settings

if sys.platform == "win32":
    # Dramatiq's AsyncIO middleware runs actors on a dedicated background
    # thread that creates its own event loop via the platform's default
    # policy -- Windows defaults to ProactorEventLoopPolicy, which psycopg's
    # async driver cannot use (the same constraint tests/conftest.py already
    # documents for pytest-asyncio's loop). Unlike `uvicorn --reload`, which
    # incidentally ends up on a compatible loop for its own reasons, the
    # `dramatiq` CLI has no such accident to lean on, so this must be set
    # explicitly before the AsyncIO middleware's thread is ever started.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Constructing RedisBroker does not connect to Redis -- the underlying
# redis-py connection pool is lazy, so importing this module (and therefore
# declaring actors against it) never requires a live Redis server. This
# keeps the normal offline pytest suite from needing Redis at all.
_settings = get_settings()
# RedisBroker.__init__ ships no parameter annotations (dramatiq has a
# py.typed marker but this constructor itself is untyped) -- the ignore is
# for that gap, not a suppression of a real type error.
broker = RedisBroker(url=_settings.redis_url)  # type: ignore[no-untyped-call]
# Not part of Dramatiq's default middleware -- required explicitly to run
# actors defined as `async def`. No results backend is configured: job
# status lives entirely in PostgreSQL (see app/models/sync_job.py).
broker.add_middleware(AsyncIO())
dramatiq.set_broker(broker)
