import time

from eventkit import Event

array1 = list(range(10))
array2 = list(range(100, 110))
array3 = list(range(200, 210))


class TestTiming:
    async def test_delay(self):
        delay = 0.01
        src = Event.sequence(array1, interval=0.01)
        e1 = src.timestamp().pluck(0)
        e2 = src.delay(delay).timestamp().pluck(0)
        r = await e1.zip(e2).map(lambda a, b: b - a).mean().list()
        assert abs(r[-1]) < delay + 0.002

    async def test_sample(self):
        timer = Event.timer(0.021, 4)
        event = Event.range(10, interval=0.01).sample(timer)
        assert await event.list() == [2, 4, 6, 8]

    async def test_timeout(self):
        timer = Event.timer(10, count=1)
        event = timer.timeout(0.01)
        assert await event.list() == [Event.NO_VALUE]

    async def test_debounce(self):
        event = (
            Event.range(10, interval=0.05)
            .mergemap(lambda t: Event.sequence(array2, 0.001))
            .debounce(0.02)
        )
        res = await event.list()
        assert res == [109] * 10

    async def test_debounce_on_first(self):
        event = (
            Event.range(10, interval=0.05)
            .mergemap(lambda t: Event.sequence(array2, 0.001))
            .debounce(0.02, on_first=True)
        )
        assert await event.list() == [100] * 10

    async def test_throttle(self):
        t0 = time.time()
        a = list(range(500))
        result = (
            await Event.sequence(a).throttle(1000, 0.1, cost_func=lambda i: 10).list()
        )
        assert result == a
        dt = time.time() - t0
        assert abs(dt - 0.5) < 0.05
