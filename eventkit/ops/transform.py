from __future__ import annotations

import asyncio
import copy
import time
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import Executor
from typing import Any

from ..event import Event, event_class
from ..util import NO_VALUE, get_event_loop, schedule_awaitable
from .combine import AddableJoinOp, Chain, Concat, Merge, Switch
from .op import Op


@event_class
class Constant(Op):
    _constant: Any

    def __init__(self, constant, source=None):
        Op.__init__(self, source)
        self._constant = constant

    def on_source(self, *args):
        self.emit(self._constant)


@event_class
class Iterate(Op):
    _it: Iterator[Any]

    def __init__(self, it, source=None):
        Op.__init__(self, source)
        self._it = iter(it)

    def on_source(self, *args):
        try:
            value = next(self._it)
            self.emit(value)
        except StopIteration:
            self._detach()
            self.set_done()


@event_class
class Enumerate(Op):
    _step: Any
    _i: Any

    def __init__(self, start=0, step=1, source=None):
        Op.__init__(self, source)
        self._i = start
        self._step = step

    def on_source(self, *args):
        self.emit(self._i, *args)
        self._i += self._step


@event_class
class Timestamp(Op):
    def on_source(self, *args):
        self.emit(time.time(), *args)


@event_class
class Partial(Op):
    _left_args: tuple[Any, ...]

    def __init__(self, *left_args, source=None):
        Op.__init__(self, source)
        self._left_args = left_args

    def on_source(self, *args):
        self.emit(*(self._left_args + args))


@event_class
class PartialRight(Op):
    _right_args: tuple[Any, ...]

    def __init__(self, *right_args, source=None):
        Op.__init__(self, source)
        self._right_args = right_args

    def on_source(self, *args):
        self.emit(*(args + self._right_args))


@event_class
class Star(Op):
    def on_source(self, arg):
        self.emit(*arg)


@event_class
class Pack(Op):
    def on_source(self, *args):
        self.emit(args)


@event_class
class Pluck(Op):
    _selections: list[Any]

    def __init__(self, *selections, source=None):
        Op.__init__(self, source)
        self._selections = []  # list of [arg-index, *sub-attributes]
        for sel in selections:
            if type(sel) is int:
                s = [sel]
            else:
                s = sel.split(".")
                if s[0].isdigit():
                    s[0] = int(s[0])
                elif s[0] == "":
                    s[0] = 0
                else:
                    s.insert(0, 0)

            self._selections.append(s)

    def on_source(self, *args):
        values = []
        for s in self._selections:
            try:
                value = args[s[0]]
                for attr in s[1:]:
                    value = getattr(value, attr)
            except Exception:
                value = NO_VALUE

            values.append(value)

        self.emit(*values)


@event_class
class Previous(Op):
    _count: int
    _q: deque[Any]

    def __init__(self, count=1, source=None):
        Op.__init__(self, source)
        self._count = count
        self._q = deque()

    def on_source(self, *args):
        self._q.append(args)
        if len(self._q) > self._count:
            self.emit(*self._q.popleft())


@event_class
class Copy(Op):
    def on_source(self, *args):
        self.emit(*(copy.copy(a) for a in args))


@event_class
class Deepcopy(Op):
    def on_source(self, *args):
        self.emit(*copy.deepcopy(args))


@event_class
class Chunk(Op):
    _size: int
    _list: list[Any]

    def __init__(self, size, source=None):
        Op.__init__(self)
        self._size = size
        self._list = []
        if source is not None:
            self.set_source(source)

    def on_source(self, *args):
        self._list.append(args[0] if len(args) == 1 else args or NO_VALUE)
        if len(self._list) == self._size:
            self.emit(self._list)
            self._list = []

    def on_source_done(self, source):
        if self._list:
            self.emit(self._list)

        Op.on_source_done(self, self._source)


@event_class
class ChunkWith(Op):
    _timer: Event | None
    _list: list[Any]
    _emit_empty: bool

    def __init__(self, timer, emit_empty, source=None):
        Op.__init__(self)
        self._timer = timer
        self._emit_empty = emit_empty
        self._list = []
        timer.connect(self._on_timer, self.on_source_error, self.on_source_done)
        if source is not None:
            self.set_source(source)

    def on_source(self, *args):
        self._list.append(args[0] if len(args) == 1 else args or NO_VALUE)

    def _on_timer(self, *args):
        if self._list or self._emit_empty:
            self.emit(self._list)
            self._list = []

    def on_source_done(self, source):
        if self._list:
            self.emit(self._list)
            self._list = []

        if self._timer is not None:
            self._timer.disconnect(
                self._on_timer, self.on_source_error, self.on_source_done
            )
            self._timer = None

        Op.on_source_done(self, self._source)


@event_class
class Map(Op):
    _func: Callable[..., Any]
    _timeout: float
    _ordered: bool
    _task_limit: int | None
    _coro_q: deque[Any]
    _tasks: deque[asyncio.Future[Any]]

    def __init__(self, func, timeout=0, ordered=True, task_limit=None, source=None):
        Op.__init__(self)
        self._func = func
        self._timeout = timeout
        self._ordered = ordered
        self._task_limit = task_limit
        self._coro_q = deque()
        self._tasks = deque()
        if source is not None:
            self.set_source(source)

    def on_source(self, *args):
        obj = self._func(*args)
        if hasattr(obj, "__await__"):
            # function returns an awaitable
            if not self._task_limit or len(self._tasks) < self._task_limit:
                # schedule right away
                self._create_task(obj)
            else:
                # queue for later
                self._coro_q.append(obj)
        else:
            # regular function returns the result directly
            self.emit(obj)

    def on_source_done(self, source):
        if not self._tasks:
            # only end when no tasks are pending
            Op.on_source_done(self, self._source)

        self._source = None

    def _create_task(self, awaitable):
        # schedule a task to be run
        if self._timeout:
            awaitable = asyncio.wait_for(awaitable, self._timeout)

        task = schedule_awaitable(awaitable)
        task.add_done_callback(self._on_task_done)
        self._tasks.append(task)

    def _on_task_done(self, task):
        # handle task result
        tasks = self._tasks
        if self._ordered:
            while tasks and tasks[0].done():
                # remove task after emitting result
                task = tasks[0]
                self._emit_task(task)
                task = tasks.popleft()
        else:
            # remove task after emitting result
            self._emit_task(task)
            tasks.remove(task)

        # schedule pending awaitables from the queue
        while self._coro_q and (not self._task_limit or len(tasks) < self._task_limit):
            self._create_task(self._coro_q.popleft())

        # end when source has ended with no pending tasks
        if not tasks and self._source is None:
            Op.on_source_done(self, self._source)

    def _emit_task(self, task):
        if task.cancelled():
            return  # e.g. by Event.run() cleanup: no value, no error
        try:
            result = task.result()
        except Exception as error:
            # Like a failing synchronous function: the error, and no value.
            self.error_event.emit(self, error)
            return
        self.emit(result)


def _run_chunk(func, chunk):
    """Run ``func`` on each argument tuple of ``chunk`` in an executor
    worker: ``(True, result)`` or ``(False, exception)`` per value, so one
    failing value does not fail the others. Module level, so a process
    pool can pickle it."""
    outcomes = []
    for args in chunk:
        try:
            outcomes.append((True, func(*args)))
        except Exception as error:
            outcomes.append((False, error))
    return outcomes


@event_class
class PoolMap(Map):
    """``map`` on a :class:`concurrent.futures.Executor`; see
    :meth:`Event.poolmap`."""

    _executor: Executor
    _chunksize: int
    _chunk: list[tuple[Any, ...]]

    def __init__(
        self, executor, func, timeout=None, chunksize=1, ordered=True, source=None
    ):
        if chunksize < 1:
            raise ValueError(f"chunksize must be at least 1, not {chunksize}")
        Map.__init__(self, func, timeout or 0, ordered)
        self._executor = executor
        self._chunksize = chunksize
        self._chunk = []
        if source is not None:
            self.set_source(source)

    def on_source(self, *args):
        self._chunk.append(args)
        if len(self._chunk) >= self._chunksize:
            self._submit_chunk()

    def on_source_done(self, source):
        if self._chunk:
            self._submit_chunk()  # the last, partial chunk
        Map.on_source_done(self, source)

    def _submit_chunk(self):
        chunk, self._chunk = self._chunk, []
        future = self._executor.submit(_run_chunk, self._func, chunk)
        # Cancelling the asyncio future (timeout, Event.run() cleanup) also
        # cancels the executor's, if it has not started.
        self._create_task(asyncio.wrap_future(future, loop=get_event_loop()))

    def _emit_task(self, task):
        if task.cancelled():
            return
        try:
            outcomes = task.result()
        except Exception as error:  # the whole chunk: timeout, broken pool
            self.error_event.emit(self, error)
            return
        for ok, value in outcomes:
            if ok:
                self.emit(value)
            else:
                self.error_event.emit(self, value)


@event_class
class Emap(Op):
    _constr: Callable[..., Any]
    _joiner: AddableJoinOp | None

    def __init__(self, constr, joiner, source=None):
        Op.__init__(self)
        self._constr = constr
        self._joiner = joiner
        # Subscribe before linking the joiner to the source: with a source
        # that has already ended, the joiner ends right away.
        joiner.connect(self.emit, self.error_event.emit, self._on_joiner_done)
        if source is not None:
            self.set_source(source)

    def set_source(self, source):
        source = Event.create(source)
        if self._source is None and self._joiner is not None:
            # The source is the joiner's parent: its nested events go there,
            # and the joiner ends once the source and they have all ended.
            self._joiner.set_parent(source)
        Op.set_source(self, source)

    def on_source(self, *args):
        obj = self._constr(*args)
        event = self.create(obj)
        if self._joiner is not None:  # None once it has ended
            self._joiner.add_source(event)

    def on_source_done(self, source):
        pass

    def _on_joiner_done(self, joiner):
        joiner.disconnect(self.emit, self.error_event.emit, self._on_joiner_done)
        self._joiner = None
        self._detach()
        self.set_done()


@event_class
class Mergemap(Emap):
    def __init__(self, constr, source=None):
        Emap.__init__(self, constr, Merge(), source)


@event_class
class Chainmap(Emap):
    def __init__(self, constr, source=None):
        Emap.__init__(self, constr, Chain(), source)


@event_class
class Concatmap(Emap):
    def __init__(self, constr, source=None):
        Emap.__init__(self, constr, Concat(), source)


@event_class
class Switchmap(Emap):
    def __init__(self, constr, source=None):
        Emap.__init__(self, constr, Switch(), source)
