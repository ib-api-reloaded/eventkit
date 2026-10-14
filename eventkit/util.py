"""Eventkit utilities."""

import asyncio
import datetime as dt
import sys
import threading
import warnings
from collections.abc import AsyncIterator
from typing import Final


class _NoValue:
    def __bool__(self):
        return False

    def __repr__(self):
        return "<NoValue>"

    __str__ = __repr__


NO_VALUE: Final = _NoValue()

_MAIN_THREAD_ID: Final = threading.main_thread().ident


if sys.version_info >= (3, 14):

    def _thread_loop() -> asyncio.AbstractEventLoop | None:
        """The loop set for this thread, or None (3.14+ never creates one)."""
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            return None

else:

    def _thread_loop() -> asyncio.AbstractEventLoop | None:
        """The loop set for this thread, or None.

        The policy API is the only public way to read it before 3.14 and
        is not deprecated there. On 3.12 / 3.13 the main thread's first
        call creates and sets a loop with a DeprecationWarning; the loop
        is the one we would create anyway, so the warning is suppressed.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            try:
                return asyncio.get_event_loop_policy().get_event_loop()
            except RuntimeError:
                return None


def get_event_loop() -> asyncio.AbstractEventLoop:
    """Return the event loop for the calling thread.

    In order of preference:

    1. the running loop;
    2. the loop set for this thread (``asyncio.set_event_loop``), so an
       application's own loop is always the one used;
    3. otherwise a new loop, which is set for this thread so asyncio and
       every caller of this function share it.

    A closed loop is replaced the same way as a missing one. No
    deprecated asyncio API is used and no DeprecationWarning is emitted
    on any supported Python version.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        current = _thread_loop()
        if current is None or current.is_closed():
            current = asyncio.new_event_loop()
            asyncio.set_event_loop(current)
        loop = current
    if threading.get_ident() == _MAIN_THREAD_ID:
        _main_thread.loop = loop
    return loop


class _MainThread:
    """The loop the main thread last used, for other threads to hand work
    to (:func:`main_thread_loop`)."""

    loop: asyncio.AbstractEventLoop | None = None


_main_thread = _MainThread()


def main_thread_loop() -> asyncio.AbstractEventLoop:
    """The main thread's event loop, from any thread.

    In the main thread this is :func:`get_event_loop`. In another thread
    it is the loop the main thread last used through eventkit; a thread
    never gets a loop of its own here, since nothing would run it.
    """
    if threading.get_ident() == _MAIN_THREAD_ID:
        return get_event_loop()
    loop = _main_thread.loop
    if loop is None or loop.is_closed():
        raise RuntimeError(
            "the main thread has no event loop in use by eventkit; "
            "call emit_threadsafe from a thread once the main thread's "
            "loop is set up"
        )
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
