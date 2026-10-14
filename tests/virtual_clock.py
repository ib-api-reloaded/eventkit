"""An asyncio event loop on a virtual clock, for deterministic timing tests.

Timers fire in exactly the order and at exactly the times they are
scheduled for, and a test never sleeps: whenever the loop would wait for
the next timer, the clock jumps straight to it. Results therefore do not
depend on machine load or scheduler jitter, and a test of a one-second
interval finishes instantly.
"""

import asyncio
import selectors


class _InstantSelector(selectors.BaseSelector):
    """Polls without blocking and advances the loop's clock by the time it
    would otherwise have waited."""

    def __init__(self, loop: "VirtualClockLoop"):
        self._loop = loop
        self._selector = selectors.DefaultSelector()

    def register(self, fileobj, events, data=None):
        return self._selector.register(fileobj, events, data)

    def unregister(self, fileobj):
        return self._selector.unregister(fileobj)

    def modify(self, fileobj, events, data=None):
        return self._selector.modify(fileobj, events, data)

    def select(self, timeout=None):
        ready = self._selector.select(0)
        if not ready and timeout:
            self._loop.advance(timeout)
        return ready

    def get_map(self):
        return self._selector.get_map()

    def close(self):
        self._selector.close()


class VirtualClockLoop(asyncio.SelectorEventLoop):
    """Selector event loop whose ``time()`` only moves when the loop
    would otherwise wait."""

    def __init__(self):
        self._now = 0.0
        super().__init__(selector=_InstantSelector(self))

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        # Land exactly on the next timer's deadline: `now + seconds` can
        # round to just short of it, and the loop would then wait a
        # vanishing amount that rounding swallows, so time never moves.
        target = self._now + seconds
        if self._scheduled:
            target = max(target, self._scheduled[0].when())
        self._now = target


class VirtualClockTestCase:
    """Mixin for ``unittest.TestCase``: each test runs with a fresh
    :class:`VirtualClockLoop` set as the thread's event loop."""

    def setUp(self):
        super().setUp()
        self.loop = VirtualClockLoop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        asyncio.set_event_loop(None)
        self.loop.close()
        super().tearDown()
