"""Transport unsubscription must not own graph cancellation."""

import asyncio

from app.graphs.harness.runner import managed_stream


def test_stream_unsubscription_leaves_producer_running():
    async def scenario():
        release, finished = asyncio.Event(), asyncio.Event()

        async def source():
            yield "first"
            await release.wait()
            finished.set()
            yield "done"

        stream = managed_stream("disconnect-test", source)
        assert await anext(stream) == "first"
        await stream.aclose()
        release.set()
        await asyncio.wait_for(finished.wait(), 1)

    asyncio.run(scenario())


def test_a_full_final_queue_does_not_drop_an_event():
    async def scenario():
        async def source():
            for index in range(256):
                yield str(index)

        events = [event async for event in managed_stream("full-final-queue", source)]
        assert events == [str(index) for index in range(256)]

    asyncio.run(scenario())
