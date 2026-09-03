import asyncio
import queue

async def main():
    q = queue.Queue()
    async def drain():
        try:
            print("starting to_thread")
            await asyncio.to_thread(q.get)
            print("finished to_thread")
        except asyncio.CancelledError:
            print("caught CancelledError")

    t = asyncio.create_task(drain())
    await asyncio.sleep(0.5)
    t.cancel()
    try:
        await t
    except Exception as e:
        print("outer caught", type(e))

asyncio.run(main())
print("done")
