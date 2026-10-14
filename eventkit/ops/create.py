import asyncio
import itertools

from ..event import Event
from ..util import NO_VALUE, schedule_awaitable, timerange
from .op import Op


def _cancel_unless_loop_closed(task) -> None:
    """Cancel a still-running task from a finalizer. A task whose loop has
    been closed is left alone: cancelling it would schedule on the closed
    loop and raise, and a finalizer must not raise."""
    if task is not None and not task.done() and not task.get_loop().is_closed():
        task.cancel()


class Wait(Event):
    __slots__ = ("_task",)

    def __init__(self, future, name="wait"):
        Event.__init__(self, name)
        if future.done():
            self._task = None
            self.set_done()
        else:
            task = schedule_awaitable(future)
            # Only a task started here is this event's to cancel; a future
            # passed in belongs to the caller and is only watched.
            self._task = task if task is not future else None
            task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task):
        self._task = None
        if task.cancelled():
            self.set_done()
            return
        try:
            result = task.result()
        except Exception as error:
            result = NO_VALUE
            self.error_event.emit(self, error)

        self.emit(result)
        self.set_done()

    def __del__(self):
        _cancel_unless_loop_closed(self._task)


class Aiterate(Event):
    __slots__ = ("_task",)

    def __init__(self, ait):
        Event.__init__(self, ait.__qualname__)

        self._task = schedule_awaitable(self._looper(ait))
        # A task cancelled before its first step never runs the looper's
        # ``finally``; this ends the event in that case too.
        self._task.add_done_callback(self._on_task_done)

    async def _looper(self, ait):
        try:
            async for args in ait:
                self.emit(args)
        except Exception as error:
            self.error_event.emit(self, error)
        finally:
            # Also when cancelled, so whatever follows this source ends.
            self._task = None
            self.set_done()

    def _on_task_done(self, _task):
        self._task = None
        self.set_done()

    def __del__(self):
        _cancel_unless_loop_closed(self._task)


class Sequence(Aiterate):
    __slots__ = ()

    def __init__(self, values, interval=0, times=None):
        async def sequence():
            loop = asyncio.get_running_loop()
            t0 = loop.time()
            if times is not None:
                for t, value in zip(times, values):
                    await asyncio.sleep(max(0, t0 + t - loop.time()))
                    yield value
            else:
                for i, value in enumerate(values):
                    await asyncio.sleep(max(0, t0 + i * interval - loop.time()))
                    yield value

        Aiterate.__init__(self, sequence())


class Repeat(Sequence):
    __slots__ = ()

    def __init__(self, value, count, interval=0, times=None):
        Sequence.__init__(self, itertools.repeat(value, count), interval, times)


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
            loop = asyncio.get_running_loop()
            t0 = loop.time()
            i = 0
            while count is None or i < count:
                i += 1
                await asyncio.sleep(t0 + i * interval - loop.time())
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
