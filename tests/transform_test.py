import asyncio
from collections import namedtuple

import numpy as np

from eventkit import Event

array = list(range(20))


class TestTransform:
    async def test_constant(self):
        res = await Event.sequence(array).constant(42).list()
        assert res == [42] * len(array)

    async def test_previous(self):
        res = await Event.sequence(array).previous(2).list()
        assert res == array[:-2]

    async def test_iterate(self):
        res = await Event.sequence(array).iterate([5, 4, 3, 2, 1]).list()
        assert res == [5, 4, 3, 2, 1]

    async def test_count(self):
        s = "abcdefghij"
        res = await Event.sequence(s).count().list()
        assert res == array[: len(s)]

    async def test_enumerate(self):
        s = "abcdefghij"
        res = await Event.sequence(s).enumerate().list()
        assert res == list(enumerate(s))

    async def test_timestamp(self):
        interval = 0.002
        event = Event.sequence(array, interval=interval).timestamp()
        times = await event.pluck(0).list()
        std = np.std(np.diff(times) - interval)
        assert std < interval

    async def test_partial(self):
        res = await Event.sequence(array).partial(42).list()
        assert res == [(42, i) for i in array]

    async def test_partial_right(self):
        res = await Event.sequence(array).partial_right(42).list()
        assert res == [(i, 42) for i in array]

    async def test_star(self):
        def f(i, j):
            r.append((i, j))

        r = []
        event = Event.sequence(array).map(lambda i: (i, i)).star().connect(f)
        res = await event.list()
        assert res == r

    async def test_pack(self):
        event = Event.sequence(array).pack()
        res = await event.list()
        assert res == [(i,) for i in array]

    async def test_pluck(self):
        Person = namedtuple("Person", "name address")
        Address = namedtuple("Address", "city street number zipcode")
        data = [
            Person("Max", Address("Delft", "Levelstreet", "3", "2333AS")),
            Person("Elena", Address("Leiden", "Punt", "122", "2412DE")),
            Person("Fem", Address("Rotterdam", "Burgundy", "12", "3001RT")),
        ]

        def event():
            return Event.sequence(data)

        res = await event().pluck("0.name", ".address.street").list()
        assert res == [(d.name, d.address.street) for d in data]

    async def test_sync_map(self):
        res = await Event.sequence(array).map(lambda x: x * x).list()
        assert res == [i * i for i in array]

    async def test_sync_star_map(self):
        event = Event.sequence(array)
        res = (
            await event.map(lambda i: (i, i)).star().map(lambda x, y: x / 2 - y).list()
        )
        assert res == [x / 2 - y for x, y in zip(array, array)]

    async def test_async_map(self):
        async def coro(x):
            await asyncio.sleep(0.1)
            return x * x

        res = await Event.sequence(array).map(coro).list()
        assert res == [i * i for i in array]

    async def test_async_map_unordered(self):
        class A:
            def __init__(self):
                self.t = 0.1

            async def coro(self, x):
                self.t -= 0.01
                await asyncio.sleep(self.t)
                return x * x

        a = A()
        result = await Event.range(10).map(a.coro, ordered=False).list()
        expected = set(i * i for i in reversed(range(10)))
        assert result, expected

    async def test_mergemap(self):
        marbles = ["A   B    C    D", "_1   2  3    4", "__K   L     M   N"]
        res = await Event.range(3).mergemap(lambda v: Event.marble(marbles[v])).list()
        assert res == ["A", "1", "K", "B", "2", "L", "3", "C", "M", "4", "D", "N"]

    async def test_mergemap2(self):
        a = ["ABC", "UVW", "XYZ"]
        res = (
            await Event.range(3, interval=0.01)
            .mergemap(lambda v: Event.sequence(a[v], 0.05 * v))
            .list()
        )
        assert res == ["A", "B", "C", "U", "X", "V", "W", "Y", "Z"]

    async def test_concatmap(self):
        marbles = [
            "A    B    C    D",
            "_       1    2    3    4",
            "__                  K    L      M   N",
        ]
        res = await Event.range(3).concatmap(lambda v: Event.marble(marbles[v])).list()
        assert res == ["A", "B", "1", "2", "3", "K", "L", "M", "N"]

    async def test_chainmap(self):
        marbles = [
            "A    B    C    D           ",
            "_       1    2    3    4",
            "__                  K    L      M   N",
        ]
        res = await Event.range(3).chainmap(lambda v: Event.marble(marbles[v])).list()
        assert res == ["A", "B", "C", "D", "1", "2", "3", "4", "K", "L", "M", "N"]

    async def test_switchmap(self):
        marbles = [
            "A    B    C    D           ",
            "_                 K    L      M   N",
            "__      1    2      3    4",
        ]
        res = await Event.range(3).switchmap(lambda v: Event.marble(marbles[v])).list()
        assert res == ["A", "B", "1", "2", "K", "L", "M", "N"]
