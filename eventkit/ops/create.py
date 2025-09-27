import asyncio
import itertools
import logging
import time

from ..event import Event
from ..util import NO_VALUE, get_event_loop, timerange
from .op import Op

logger: logging.Logger = logging.getLogger(__name__)


class Wait(Event):
    __slots__ = ("_task",)

    def __init__(self, future, name="wait"):
        Event.__init__(self, name)
        if future.done():
            self._task = None
            self.set_done()
        else:
            loop = get_event_loop()
            self._task = asyncio.ensure_future(future, loop=loop)
            future.add_done_callback(self._on_task_done)

    def _on_task_done(self, task):
        try:
            result = task.result()
        except Exception as error:
            result = NO_VALUE
            self.error_event.emit(self, error)

        self.emit(result)
        self._task = None
        self.set_done()

    def __del__(self):
        if self._task:
            self._task.cancel()

    def cancel(self):
        if self._task and not self._task.done():
            self._task.cancel()


class Aiterate(Event):
    __slots__ = ("_task", "_ait")

    def __init__(self, ait):
        Event.__init__(self, ait.__qualname__)
        self._ait = ait
        self._task = None

    async def _looper(self):
        try:
            async for args in self._ait:
                self.emit(args)
        except asyncio.CancelledError:
            # Task was cancelled, clean up and exit gracefully
            logger.debug(f"Aiterate task for {self.name()} cancelled.")
        except Exception as error:
            self.error_event.emit(self, error)
        finally:
            self.set_done()

    def connect(self, *args, **kwargs):
        if self._task is None:
            loop = get_event_loop()
            self._task = loop.create_task(self._looper())
        return super().connect(*args, **kwargs)

    def cancel(self):
        """
        Explicitly cancels the underlying asyncio task and sets the event as done.
        """
        if self._task and not self._task.done():
            self._task.cancel()
            logger.debug(f"Aiterate task for {self.name()} requested cancellation.")
        self.set_done()  # Re-enabled to fix race condition

    def __del__(self):
        # The __del__ method is unreliable for timely resource cleanup.
        # Explicit `cancel()` method should be preferred.
        # However, keeping this for robustness in case `cancel()` is not called.
        if self._task:
            self._task.cancel()


class Sequence(Aiterate):
    __slots__ = ()

    def __init__(self, values, interval=0, times=None):
        async def sequence():
            t0 = time.time()
            if times is not None:
                for t, value in zip(times, values):
                    delay = max(0, time.time() + t - t0)
                    await asyncio.sleep(delay)
                    yield value
            else:
                for i, value in enumerate(values):
                    delay = max(0, i * interval + t0 - time.time())
                    await asyncio.sleep(delay)
                    yield value

        Aiterate.__init__(self, sequence())


class Repeat(Sequence):
    __slots__ = ()

    def __init__(self, value, count, interval=0, times=None):
        Sequence.__init__(self, itertools.repeat(count), interval, times)


class Range(Sequence):
    __slots__ = ()

    def __init__(self, *args, interval=0, times=None):
        Sequence.__init__(self, range(*args), interval, times)


class Timerange(Aiterate):
    __slots__ = ()

    def __init__(self, start=0, end=None, step=1):
        Aiterate.__init__(self, timerange(start, end, step))


class Timer(Aiterate):
    __slots__ = ()

    def __init__(self, interval, count=None):
        async def timer():
            t0 = time.time()
            i = 0
            while count is None or i < count:
                i += 1
                delay = i * interval + t0 - time.time()
                await asyncio.sleep(delay)
                yield i * interval

        Aiterate.__init__(self, timer())


class Marble(Op):
    __slots__ = ()

    def __init__(self, s, interval=0, times=None):
        s = s.replace("_", "")
        source = (
            Event.sequence(s, interval, times)
            .filter(lambda c: c not in "- ")
            .takewhile(lambda c: c != "|")
        )
        Op.__init__(self, source)
