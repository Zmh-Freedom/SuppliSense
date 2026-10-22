"""Isolated process harness using production API routes/auth and WS subscriber."""

import asyncio
from contextlib import asynccontextmanager

from app.core.config import settings
from app.main import app
from app.services import realtime

if settings.PG_DB != "delivery_stage3_test":
    raise RuntimeError("process harness requires delivery_stage3_test")


@asynccontextmanager
async def lifespan(_app):
    # Schema/user setup belongs to the parent fixture, never to two API
    # processes racing on startup. All production routes/auth are unchanged.
    realtime.start_realtime_subscriber()
    yield
    await asyncio.to_thread(realtime.stop_realtime_subscriber)


app.router.lifespan_context = lifespan


@app.post("/__test/disconnect-realtime")
def disconnect_realtime():
    pubsub = realtime._subscriber._pubsub
    if pubsub is not None:
        pubsub.connection.disconnect()
    return {"disconnected": pubsub is not None}
