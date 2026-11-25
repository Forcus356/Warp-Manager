#!/usr/bin/env python3
"""Expose the container's loopback WARP proxy, without a direct fallback.
SPDX-License-Identifier: GPL-3.0-only
"""
import asyncio
import contextlib


async def relay(reader, writer):
    other = None
    tasks = []
    try:
        source, other = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", 40000), 5)

        async def pump(src, dst):
            while True:
                data = await src.read(65536)
                if not data:
                    if dst.can_write_eof():
                        dst.write_eof()
                        await dst.drain()
                    return
                dst.write(data)
                await dst.drain()

        tasks = [asyncio.create_task(pump(reader, other)),
                 asyncio.create_task(pump(source, writer))]
        await asyncio.gather(*tasks)
    except (OSError, ConnectionError, asyncio.TimeoutError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for item in (writer, other):
            if item:
                item.close()
                with contextlib.suppress(OSError, ConnectionError):
                    await item.wait_closed()


async def main():
    async with await asyncio.start_server(relay, "0.0.0.0", 1080) as server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
