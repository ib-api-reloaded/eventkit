"""Eventkit utilities."""

import asyncio
import datetime as dt
import threading
from collections.abc import AsyncIterator
from typing import Final

_thread_local = threading.local()


class _NoValue:
    def __bool__(self):
        return False

    def __repr__(self):
        return "<NoValue>"

    __str__ = __repr__


NO_VALUE: Final = _NoValue()


def get_event_loop():
    """Get the running event loop, or create/cache one for sync contexts."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        loop = getattr(_thread_local, "loop", None)
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            _thread_local.loop = loop
            # Keep the policy in sync so asyncio.gather() et al. work
            # in sync contexts. set_event_loop is deprecated (removal
            # targeted for 3.16) but still needed for interop.
            asyncio.set_event_loop(loop)
        return loop


def schedule_awaitable(awaitable):
    """Schedule an awaitable on the event loop.

    Handles coroutines, Futures, Tasks, and generic awaitables.
    Works in both sync and async contexts. Replaces deprecated
    asyncio.ensure_future().
    """
    if asyncio.isfuture(awaitable):
        return awaitable
    if not asyncio.iscoroutine(awaitable):

        async def _wrap(a=awaitable):
            return await a

        awaitable = _wrap()
    return get_event_loop().create_task(awaitable)


async def timerange(start=0, end=None, step: float = 1) -> AsyncIterator[dt.datetime]:
    """
    Iterator that waits periodically until certain time points are
    reached while yielding those time points.

    Args:
        start: Start time, can be specified as:

            * ``datetime.datetime``.
            * ``datetime.time``: Today is used as date.
            * ``int`` or ``float``: Number of seconds relative to now.
              Values will be quantized to the given step.
        end: End time, can be specified as:

            * ``datetime.datetime``.
            * ``datetime.time``: Today is used as date.
            * ``None``: No end limit.
        step: Number of seconds, or ``datetime.timedelta``,
            to space between values.
    """
    tz = getattr(start, "tzinfo", None)
    now = dt.datetime.now(tz)
    if isinstance(step, dt.timedelta):
        delta = step
        step = delta.total_seconds()
    else:
        delta = dt.timedelta(seconds=step)
    t = start
    if t == 0 or isinstance(t, (int, float)):
        t = now + dt.timedelta(seconds=t)
        # quantize to step
        t = dt.datetime.fromtimestamp(step * int(t.timestamp() / step))
    elif isinstance(t, dt.time):
        t = dt.datetime.combine(now.today(), t)

    if t < now:
        # t += delta
        t -= ((t - now) // delta) * delta

    if isinstance(end, dt.time):
        end = dt.datetime.combine(now.today(), end)
    elif isinstance(end, (int, float)):
        end = now + dt.timedelta(seconds=end)

    while end is None or t <= end:
        now = dt.datetime.now(tz)
        secs = (t - now).total_seconds()
        await asyncio.sleep(secs)
        yield t
        t += delta
