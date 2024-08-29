import asyncio

from eventkit import Event

array = list(range(10))


class TestAggregate:
    async def test_min(self):
        res = await Event.sequence(array).min().list()
        assert res == [0] * 10

    async def test_max(self):
        res = await Event.sequence(array).max().list()
        assert res == array

    async def test_sum(self):
        res = await Event.sequence(array).sum().list()
        assert res == [0, 1, 3, 6, 10, 15, 21, 28, 36, 45]

    async def test_product(self):
        res = await Event.sequence(array[1:]).product().list()
        assert res == [1, 2, 6, 24, 120, 720, 5040, 40320, 362880]

    async def test_any(self):
        res = await Event.sequence(array).any().list()
        assert res == [False, True, True, True, True, True, True, True, True, True]

    async def test_all(self):
        x = [True] * 10 + [False] * 10
        res = await Event.sequence(x).all().list()
        assert res == x

    async def test_pairwaise(self):
        res = await Event.sequence(array).pairwise().list()
        assert res == list(zip(array, array[1:]))

    async def test_chunk(self):
        res = await Event.sequence(array).chunk(3).list()
        assert res == [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]]

    async def test_chunkwith(self):
        timer = Event.timer(0.029, 10)
        res = await Event.sequence(array, 0.01).chunkwith(timer).list()
        assert res == [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]]
        await asyncio.sleep(0.5)

    async def test_array(self):
        res = await Event.sequence(array).array(5).last().list()
        assert list(res[0]) == array[-5:]
