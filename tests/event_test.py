import asyncio
import gc
import platform

import eventkit as ev
from eventkit import Event


class Object:
    def __init__(self):
        self.value = 0

    def method(self, x, y):
        self.value += x - y

    def __call__(self, x, y):
        self.value += x - y


async def ait(it):
    for x in it:
        await asyncio.sleep(0)
        yield x


class TestEvent:
    """Test Event"""

    def test_functor(self):
        obj1 = Object()
        obj2 = Object()
        event = Event("test")
        event += obj1
        event.emit(9, 4)
        assert obj1.value == 5
        event += obj2
        event.emit(5, 6)
        assert obj1.value == 4
        assert obj2.value == -1

        del obj2
        if platform.python_implementation() == "PyPy":
            gc.collect()
        assert len(event) == 1
        event -= obj1
        assert obj1 not in event
        assert len(event) == 0

    def test_method(self):
        obj1 = Object()
        obj2 = Object()
        event = Event("test")
        event += obj1.method
        event.emit(9, 4)
        assert obj1.value == 5
        event += obj2.method
        event.emit(5, 6)
        assert obj1.value == 4
        assert obj2.value == -1

        del obj2
        if platform.python_implementation() == "PyPy":
            gc.collect()
        assert len(event) == 1
        event -= obj1.method
        assert obj1.method not in event
        assert len(event) == 0

        event += obj1.method
        event += obj1.method
        assert len(event) == 2
        event.disconnect_obj(obj1)
        assert len(event) == 0

    def test_function(self):
        def f1(x, y):
            nonlocal value1
            value1 += x - y

        def f2(x, y):
            nonlocal value2
            value2 += x - y

        value1 = 0
        value2 = 0
        event = Event("test")
        event += f1
        event.emit(9, 4)
        assert value1 == 5
        event += f2
        event.emit(5, 6)
        assert value1 == 4
        assert value2 == -1
        event -= f1
        assert f1 not in event
        event -= f2
        assert f2 not in event
        assert len(event) == 0

    def test_cmethod(self):
        import math

        event = Event("test")
        event += math.pow
        event.emit(2, 8)

    def test_keep_ref(self):
        import weakref

        obj = Object()
        event = Event("test")
        event.connect(obj.method, keep_ref=True)
        wr = weakref.ref(obj)
        del obj
        event.emit(9, 4)
        assert wr().value == 5
        obj = wr()
        event.emit(5, 6)
        assert obj.value == 4
        assert obj.method in event
        event -= obj.method
        assert obj.method not in event

    async def test_coro_func(self):
        async def coro(d):
            result.append(d)
            await asyncio.sleep(0)

        result = []
        event = Event("test")
        event += coro

        event.emit(4)
        event.emit(2)
        await asyncio.sleep(0)
        assert result == [4, 2]

        result.clear()
        event -= coro
        event.emit(8)
        await asyncio.sleep(0)
        assert not result

    async def test_aiter(self):
        async def coro():
            return [v async for v in event]

        a = list(range(0, 10))
        event = Event.sequence(a)
        result = await coro()
        assert result == a

    async def test_fork(self):
        res = await Event.range(4, 10)[ev.Min, ev.Max, ev.Op().sum()].zip().list()
        assert res == [
            (4, 4, 4),
            (4, 5, 9),
            (4, 6, 15),
            (4, 7, 22),
            (4, 8, 30),
            (4, 9, 39),
        ]

    def test_operator_connect(self):
        result = []
        ev1 = Event()
        ev2 = ev.Map(lambda x: x + 10)
        ev2 += result.append
        ev1 += ev2
        for i in range(10):
            ev1.emit(i)
        assert result == list(range(10, 20))

    async def test_emit_threadsafe(self):
        async def coro(d):
            result.append(d)
            await asyncio.sleep(0)

        result = []
        event = Event("test")
        event += coro

        event.emit_threadsafe(4)
        event.emit_threadsafe(2)
        await asyncio.sleep(0.1)
        assert result == [4, 2]

        result.clear()
        event -= coro
        event.emit_threadsafe(8)
        await asyncio.sleep(0)
        assert not result
