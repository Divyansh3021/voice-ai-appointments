from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from clinic_agent.config import settings


def _build_engine() -> AsyncEngine:
    # pool_pre_ping matters more than usual here: Neon's compute auto-suspends
    # after a few minutes idle, so a pooled connection can go stale between
    # calls. pre_ping catches that and reconnects instead of failing the query.
    # pool_recycle keeps us from holding a connection across a suspend cycle at
    # all. Small pool size since Neon (especially free tier) caps concurrent
    # connections and this process doesn't need many.
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=5,
        max_overflow=5,
    )


engine = _build_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def reset_engine_for_current_loop() -> None:
    """Rebuild the engine/sessionmaker bound to whichever asyncio event loop
    is currently running.

    Required in the LiveKit agent worker: a single warmed-up process
    handles many calls sequentially over its lifetime, each dispatched onto
    its own fresh event loop, but `engine` above is normally only ever
    created once at module import time - bound to whichever loop happened
    to be running for the *first* call that process handled. Every
    subsequent call in that same process was reusing pool connections tied
    to an already-closed loop, surfacing as
    "Future ... attached to a different loop" the moment a second call
    landed on a reused process.

    Deliberately does NOT call engine.dispose() on the old engine - that
    itself has to talk to the old (already-gone) loop to close connections
    gracefully, which is exactly the same failure mode. The old engine and
    its connections are simply dropped; whatever sockets it held get
    reclaimed by the OS when the process exits.

    Not needed in the FastAPI process (api/main.py), which has exactly one
    event loop for its entire lifetime - only call this from the agent
    worker's per-job entrypoint.
    """
    global engine, SessionLocal
    engine = _build_engine()
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
