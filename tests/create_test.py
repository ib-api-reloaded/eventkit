import asyncio

from eventkit import Event

array1 = list(range(10))
array2 = list(range(100, 110))


class TestCreate:
    """test create"""

    async def test_wait(self):
        async def coro():
            await asyncio.sleep(0)
            return 42

        res = await Event.wait(coro())
        assert res == 42

    async def test_aiterate(self):
        async def ait():
            await asyncio.sleep(0)
            for i in array1:
                yield i

        res = await Event.aiterate(ait()).list()
        assert res == array1

    async def test_marble(self):
        s = "   a b c   d e f"
        res = await Event.marble(s, interval=0.001).list()
        assert res == [c for c in "abcdef"]
