from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from typing import Any

from ..event import Event, event_class
from ..util import NO_VALUE, get_event_loop
from .op import Op


@event_class
class Delay(Op):
    _delay: float
    # (deadline, callback, args) in arrival order
    _pending: deque[tuple[float, Callable[..., Any], tuple[Any, ...]]]
    _handle: asyncio.TimerHandle | None

    def __init__(self, delay, source=None):
        Op.__init__(self)
        self._delay = delay
        # (deadline, callback, args) in arrival order; deadlines never
        # decrease, so the head is always the next one due.
        self._pending = deque()
        self._handle = None  # the one timer, set for the head's deadline
        if source is not None:
            self.set_source(source)

    def on_source(self, *args):
        self._schedule(self.emit, args)

    def on_source_error(self, source, error):
        self._schedule(self.error_event.emit, (source, error))

    def on_source_done(self, source):
        self._detach()
        self._schedule(self.set_done, ())

    def _schedule(self, callback, args):
        loop = get_event_loop()
        self._pending.append((loop.time() + self._delay, callback, args))
        if self._handle is None:
            self._handle = loop.call_at(self._pending[0][0], self._release)

    def _release(self):
        """Release the head (its timer just fired) and everything else
        already due, in arrival order, then re-arm for the next head.

        One ordered queue instead of a timer per value: asyncio does not
        run timers with equal deadlines in scheduling order, so separate
        timers could reorder values, or complete before the last value.
        """
        loop = get_event_loop()
        pending = self._pending
        deadline, callback, args = pending.popleft()
        limit = max(deadline, loop.time())
        self._handle = None
        try:
            callback(*args)
            while pending and pending[0][0] <= limit:
                _, callback, args = pending.popleft()
                callback(*args)
        finally:
            if pending and self._handle is None:
                self._handle = loop.call_at(pending[0][0], self._release)


@event_class
class Timeout(Op):
    _timeout: float
    _handle: asyncio.TimerHandle | None
    _last_time: float

    def __init__(self, timeout, source=None):
        Op.__init__(self)
        self._timeout = timeout
        self._last_time = get_event_loop().time()
        self._handle = None
        if source is None or not source.done():
            self._schedule()
        if source is not None:
            self.set_source(source)

    def on_source(self, *args):
        loop = get_event_loop()
        self._last_time = loop.time()
        Op.on_source(self, *args)

    def on_source_done(self, source):
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None
        Op.on_source_done(self, source)

    def _schedule(self):
        loop = get_event_loop()
        self._handle = loop.call_at(self._last_time + self._timeout, self._on_timer)

    def _on_timer(self):
        handle = self._handle
        if self.done() or handle is None:
            # Ended another way (e.g. attached to a finished source).
            return
        # Expired unless a value arrived after this timer was set: compare
        # with the deadline the timer was scheduled for, not the clock,
        # which asyncio may read up to one clock tick before it.
        if self._last_time + self._timeout <= handle.when():
            self._detach()
            self.emit()
            self.set_done()
        else:
            self._schedule()


@event_class
class Debounce(Op):
    _interval: float
    _on_first: bool
    _handle: asyncio.TimerHandle | None
    _last_time: float

    def __init__(self, interval, on_first=False, source=None):
        Op.__init__(self)
        self._interval = interval
        self._on_first = on_first
        self._last_time = -float("inf")
        self._handle = None
        if source is not None:
            self.set_source(source)

    def on_source(self, *args):
        loop = get_event_loop()
        time = loop.time()
        delta = time - self._last_time
        self._last_time = time
        if self._on_first:
            if delta >= self._interval:
                self.emit(*args)
        else:
            if self._handle:
                self._handle.cancel()
            self._handle = loop.call_at(
                time + self._interval, self._delayed_emit, *args
            )

    def _delayed_emit(self, *args):
        self._handle = None
        self.emit(*args)
        if self._source is None:
            self.set_done()

    def on_source_done(self, source):
        self._disconnect_from(source)
        self._source = None
        if not self._handle:
            self.set_done()


@event_class
class Throttle(Op):
    status_event: Event
    _maximum: float
    _inclusive: bool  # the total may reach the maximum (else stays below it)
    _interval: float
    _cost_func: Callable[..., float] | None
    _q: deque[tuple[tuple[Any, ...], float]]  # waiting (args, cost)
    _time_q: deque[float]  # emit times inside the window
    _cost_q: deque[float]  # their costs (1 each without a cost function)
    _is_throttling: bool
    _handle: asyncio.TimerHandle | None

    def __init__(
        self, maximum, interval, cost_func=None, source=None, *, inclusive=False
    ):
        Op.__init__(self)
        self.status_event = Event("throttle_status")
        """
        Sub event that emits ``True`` when throttling starts and ``False``
        when throttling ends.
        """
        self._maximum = maximum
        self._inclusive = inclusive
        self._interval = interval
        self._cost_func = cost_func
        self._q = deque()
        self._time_q = deque()
        self._cost_q = deque()
        self._is_throttling = False
        self._handle = None  # the one pending retry, if any
        if source is not None:
            self.set_source(source)

    def set_limit(self, maximum, interval, *, inclusive=None):
        """
        Dynamically update the ``maximum`` per ``interval`` limit, and
        optionally whether it is ``inclusive`` (unchanged when omitted).
        """
        self._maximum = maximum
        self._interval = interval
        if inclusive is not None:
            self._inclusive = inclusive

    def on_source(self, *args):
        # The cost is taken once, on arrival; without a cost function the
        # limit counts values.
        cost = 1.0 if self._cost_func is None else self._cost_func(*args)
        self._q.append((args, cost))
        self._try_emit()

    def on_source_done(self, source):
        self._disconnect_from(source)
        self._source = None
        if not self._q:
            self.set_done()
            self.status_event.set_done()

    def _on_retry(self):
        # The window has moved on by at least this timer's deadline, even
        # if asyncio runs the timer up to one clock tick before it.
        handle, self._handle = self._handle, None
        if handle is None:
            return
        deadline = handle.when()
        self._try_emit(deadline)

    def _try_emit(self, not_before=None):
        loop = get_event_loop()
        t = loop.time()
        if not_before is not None and not_before > t:
            t = not_before
        q = self._q
        times = self._time_q
        costs = self._cost_q

        # forget old emit times
        while times and times[0] + self._interval <= t:
            times.popleft()
            costs.popleft()

        # emit values while not exceeding the limit
        while q:
            args, cost = q[0]
            total_cost = cost + sum(costs)
            # Over budget: wait, unless the window is empty (a value whose
            # cost alone reaches the maximum then goes out on its own).
            over = (
                total_cost > self._maximum
                if self._inclusive
                else total_cost >= self._maximum
            )
            if self._maximum and over and costs:
                break
            args, cost = q.popleft()
            times.append(t)
            costs.append(cost)
            self.emit(*args)

        # update status and schedule new emits
        if q:
            if not self._is_throttling:
                self.status_event.emit(True)
            if self._handle is None:
                self._handle = loop.call_at(times[0] + self._interval, self._on_retry)
        elif self._is_throttling:
            self.status_event.emit(False)
        self._is_throttling = bool(q)

        if not q and self._source is None:
            self.set_done()
            self.status_event.set_done()


@event_class
class Sample(Op):
    _timer: Event | None

    def __init__(self, timer, source=None):
        Op.__init__(self)
        self._timer = timer
        timer.connect(self._on_timer, self.on_source_error, self.on_source_done)
        if source is not None:
            self.set_source(source)

    def on_source(self, *args):
        self._value = args

    def _on_timer(self, *args):
        if self._value is not NO_VALUE:
            self.emit(*self._value)

    def on_source_done(self, source):
        Op.on_source_done(self, self._source)
        timer = self._timer
        if timer is not None:
            timer.disconnect(self._on_timer, self.on_source_error, self.on_source_done)
            self._timer = None
