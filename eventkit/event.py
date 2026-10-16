from __future__ import annotations

import asyncio
import builtins
import functools
import itertools
import logging
import types
import weakref
from collections.abc import AsyncIterable, Awaitable, Callable, Iterable
from concurrent.futures import Executor
from dataclasses import dataclass, field
from typing import Any as AnyType
from typing import (
    ClassVar,
    TypeVar,
)

from .util import (
    NO_VALUE,
    _NoValue,
    get_event_loop,
    main_thread_loop,
    schedule_awaitable,
)

logger: logging.Logger = logging.getLogger(__name__)

_E = TypeVar("_E")


# Links from an event to the events feeding it: an operator's source, a
# higher-order join's parent, and side inputs (``sample``'s timer,
# ``take_until``'s notifier, ``chunkwith``'s timer, ``emap``'s joiner).
_UPSTREAM_LINKS = ("_source", "_parent", "_timer", "_notifier", "_joiner")


def _pipeline_nodes(roots: Iterable[Event]) -> dict[int, Event]:
    """The events in ``roots`` and every event upstream of them."""
    nodes: dict[int, Event] = {}
    stack = list(roots)
    while stack:
        node = stack.pop()
        if id(node) in nodes:
            continue
        nodes[id(node)] = node
        for attr in _UPSTREAM_LINKS:
            upstream = getattr(node, attr, None)
            if isinstance(upstream, Event):
                stack.append(upstream)
        stack.extend(getattr(node, "_sources", ()))
    return nodes


def _pipeline_tasks(nodes: dict[int, Event]) -> set[asyncio.Task]:
    """Unfinished tasks of the pipeline ``nodes`` that only this pipeline
    uses.

    Tasks are a source's ``_task`` and an async map's ``_tasks``. An
    event that also has a listener outside the pipeline (another pipeline,
    a plain callback) is in use elsewhere, so its tasks are not included.
    """
    tasks: set[asyncio.Task] = set()
    for node in nodes.values():
        if not _listened_to_only_by(node, nodes):
            continue
        task = getattr(node, "_task", None)
        if task is not None and not task.done():
            tasks.add(task)
        tasks.update(t for t in getattr(node, "_tasks", ()) if not t.done())
    return tasks


def _listened_to_only_by(event: Event, nodes: dict[int, Event]) -> bool:
    """Whether every live listener of ``event`` is one of ``nodes``."""
    for slot in event._slots.slots:
        if slot.weakref is not None:
            owner = slot.weakref()
            if owner is None:
                continue  # collected
        else:
            owner = slot.obj
        if owner is None or id(owner) not in nodes:
            return False  # a plain callback, or an event outside
    return True


@dataclass(slots=True)
class Slot:
    obj: AnyType
    weakref: Callable[[], AnyType] | None
    func: Callable[..., AnyType] | None


@dataclass(slots=True)
class Slots:
    slots: list[Slot] = field(default_factory=list)

    def add(self, obj, weakref, func) -> None:
        self.slots.append(Slot(obj, weakref, func))

    def remove(self, obj, func, *, every: bool = False):
        """Remove the earliest slot of this obj/func combination, or with
        ``every`` all of them. Removing one that is not there does nothing."""

        def matches(x):
            return (x.obj is obj or x.weakref and x.weakref() is obj) and (
                x.func is func
            )

        if every:
            self.slots = list(itertools.filterfalse(matches, self.slots))
            return
        for i, slot in enumerate(self.slots):
            if matches(slot):
                # A new list: an emit in progress iterates over a copy.
                self.slots = self.slots[:i] + self.slots[i + 1 :]
                return

    def remove_obj(self, obj):
        self.slots = list(
            itertools.filterfalse(
                lambda x: x.obj is obj or x.weakref and x.weakref() is obj,
                self.slots,
            )
        )

    def remove_ref(self, ref):
        self.slots = list(
            itertools.filterfalse(
                lambda x: x.weakref is ref,
                self.slots,
            )
        )

    def exists(self, obj, func):
        return any(
            [
                (x.obj is obj or x.weakref and x.weakref() is obj) and x.func is func
                for x in self.slots
            ]
        )

    @property
    def count(self) -> int:
        return len(self.slots)

    def clear(self) -> None:
        self.slots = []

    def __call__(self, caller, *args, **kwargs):
        """Loop over all active callbacks and call them"""
        for slot in self.slots.copy():
            ref = slot.weakref
            func = slot.func

            try:
                if ref is not None:
                    obj = ref()
                    if obj is None:
                        # Collected, its slot not yet removed (during this
                        # emit, or on PyPy until the weakref callback runs).
                        continue
                else:
                    obj = slot.obj

                result = None
                if obj is None:
                    if func is not None:
                        result = func(*args, **kwargs)
                else:
                    if func is not None:
                        result = func(obj, *args, **kwargs)
                    else:
                        result = obj(*args, **kwargs)

                # even though asyncio.iscoroutine() would also work here,
                # this manual hasattr() check performs better.
                if result and hasattr(result, "__await__"):
                    schedule_awaitable(result)
            except Exception as error:
                # It's not really clear in the documentation or usage that exceptions
                # get returned via an 'error_event' callback. We should make sure
                # people know this clearly so event handler callback errors are noticed.
                if caller._errors_have_listeners():
                    caller.error_event.emit(caller, error)
                else:
                    logger.exception(
                        "Value %s caused exception for event %s", args, caller
                    )


def event_class(cls: type[_E]) -> type[_E]:
    """Make ``cls`` a slotted dataclass in the :class:`Event` family.

    Events are compared and hashed by identity (an event is a thing, not
    a value), have no generated ``repr``, and keep their hand-written
    ``__init__``, so every constructor keeps its public signature.
    """
    return dataclass(slots=True, eq=False, repr=False, init=False)(cls)


@dataclass(slots=True, eq=False, repr=False, init=False, weakref_slot=True)
class Event:
    """
    Enable event passing between loosely coupled components.
    The event emits values to connected listeners and has
    a selection of operators to create general data flow pipelines.

    Args:
        name: Name to use for this event.
    """

    _name: str
    # The sub-events, created on first use (see error_event, done_event):
    # most events never need them.
    _error_event: Event | None
    _done_event: Event | None
    _value: AnyType
    _slots: Slots
    _done: bool
    _source: Event | None
    _task: AnyType

    NO_VALUE: ClassVar[_NoValue] = NO_VALUE

    def __init__(self, name: str = ""):
        self._name = name or type(self).__qualname__
        self._error_event = None
        self._done_event = None
        self._value = NO_VALUE
        self._slots = Slots()
        self._done = False
        self._source = None
        self._task = None

    @property
    def error_event(self) -> Event:
        """
        Sub event that emits the errors of this event as
        ``emit(source, exception)``. Created when first used.
        """
        if self._error_event is None:
            self._error_event = Event("error")
        return self._error_event

    # ``event.error_event += f`` reads, connects, and assigns the same sub
    # event back; that write is accepted, a different event is not.
    @error_event.setter
    def error_event(self, value: Event) -> None:
        if value is not self.error_event:
            raise AttributeError("error_event cannot be replaced")

    @property
    def done_event(self) -> Event:
        """
        Sub event that emits this event, once, when it ends, and then ends
        too. Created when first used; for an event that has already ended
        it is created ended.
        """
        if self._done_event is None:
            self._done_event = Event("done")
            self._done_event._done = self._done
        return self._done_event

    @done_event.setter
    def done_event(self, value: Event) -> None:
        if value is not self.done_event:
            raise AttributeError("done_event cannot be replaced")

    def _errors_have_listeners(self) -> bool:
        """Whether emitting on ``error_event`` would reach anyone (without
        creating it)."""
        return self._error_event is not None and len(self._error_event) > 0

    def name(self) -> str:
        """
        This event's name.
        """
        return self._name

    def done(self) -> bool:
        """
        ``True`` if event has ended with no more emits coming,
        ``False`` otherwise.
        """
        return self._done

    def set_done(self):
        """
        Set this event to be ended. The event should not emit anything
        after that.
        """
        if not self._done:
            self._done = True
            done_event = self._done_event
            if done_event is not None:  # nobody listens to one never created
                done_event.emit(self)
                done_event.set_done()

    def value(self):
        """
        This event's last emitted value.
        """
        v = self._value

        return NO_VALUE if v is NO_VALUE else v[0] if len(v) == 1 else v or NO_VALUE

    def connect(self, listener, error=None, done=None, keep_ref: bool = False) -> Event:
        """
        Connect a listener to this event. If the listener is added multiple
        times then it is invoked just as many times on emit.

        The ``+=`` operator can be used as a synonym for this method::

            import eventkit as ev

            def f(a, b):
                print(a * b)

            def g(a, b):
                print(a / b)

            event = ev.Event()
            event += f
            event += g
            event.emit(10, 5)

        Args:
            listener: The callback to invoke on emit of this event.
                It gets the ``*args`` from an emit as arguments.
                If the listener is a coroutine function, or a function that
                returns an awaitable, the awaitable is run in the
                asyncio event loop.
            error: The callback to invoke on error of this event.
                It gets (this event, exception) as two arguments.
            done: The callback to invoke on ending of this event.
                It gets this event as single argument.
            keep_ref:
                * ``True``: The event keeps a strong reference to the
                  callable.
                * ``False``: For a bound method, the event keeps a weak
                  reference to the method's instance. For a callable object
                  that supports weak references, the event keeps a weak
                  reference to the object. When that object is garbage
                  collected, the listener is disconnected. Functions,
                  built-ins and ``functools.partial`` objects are held
                  strongly on every interpreter.
        """
        if isinstance(listener, Op):
            # let the operator connect itself to this event
            listener.set_source(self)
            return self

        obj, func = self._split(listener)
        if not keep_ref and hasattr(obj, "__weakref__"):
            ref = weakref.ref(obj, self._onFinalize)
            obj = None
        else:
            ref = None

        self._slots.add(obj, ref, func)

        if done is not None:
            self.done_event.connect(done)

        if error is not None:
            self.error_event.connect(error)

        return self

    def disconnect(self, listener, error=None, done=None):
        """
        Undo one :meth:`connect`: remove one connection of the listener
        (and of the given error and done callbacks). A listener connected
        twice stays connected once. Disconnecting a listener that is not
        connected does nothing. An operator connected with ``connect(op)``
        is detached from this event.

        The ``-=`` operator can be used as a synonym for this method.
        :meth:`disconnect_all` removes every connection of a listener,
        :meth:`disconnect_obj` everything connected on an object, and
        :meth:`clear` all listeners.

        Args:
            listener: The callback to disconnect.
            error: The error callback to disconnect.
            done: The done callback to disconnect.
        """
        return self._disconnect(listener, error, done, every=False)

    def disconnect_all(self, listener, error=None, done=None):
        """
        Remove every connection of the listener (and of the given error
        and done callbacks), however many times it was connected.

        Args:
            listener: The callback to disconnect.
            error: The error callback to disconnect.
            done: The done callback to disconnect.
        """
        return self._disconnect(listener, error, done, every=True)

    def _disconnect(self, listener, error, done, *, every: bool):
        if isinstance(listener, Op):
            # the inverse of connect(op): detach the operator from this event
            if listener._source is self:
                listener._disconnect_from(self)
                listener._source = None
            return self

        obj, func = self._split(listener)
        self._slots.remove(obj, func, every=every)

        if self._done_event is not None and done is not None:
            self._done_event._disconnect(done, None, None, every=every)

        if self._error_event is not None and error is not None:
            self._error_event._disconnect(error, None, None, every=every)

        return self

    def disconnect_obj(self, obj):
        """
        Disconnect all listeners on the given object.
        (also the error and done listeners).

        Args:
            obj: The target object that is to be completely removed from
              this event.
        """
        self._slots.remove_obj(obj)

        if self._error_event is not None:
            self._error_event.disconnect_obj(obj)

        if self._done_event is not None:
            self._done_event.disconnect_obj(obj)

    def emit(self, *args):
        """
        Emit a new value to all connected listeners.

        Args:
            args: Argument values to emit to listeners.
        """
        self._value = args
        self._slots(self, *args)

    def emit_threadsafe(self, *args):
        """
        Threadsafe version of :meth:`emit` that doesn't invoke the
        listeners directly but via the event loop of the main thread.
        """
        main_thread_loop().call_soon_threadsafe(self.emit, *args)

    def clear(self):
        """
        Disconnect all listeners.
        """
        self._slots.clear()

    def run(self) -> builtins.list:
        """
        Start the asyncio event loop, run this event to completion and
        return all values as a list::

            import eventkit as ev

            ev.Timer(0.25, count=10).run()

        Outputs:

            [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5]

        .. note::

            ``run()`` raises ``RuntimeError`` when the thread's event loop
            is already running, as it is inside a Jupyter notebook or any
            coroutine, unless the loop has been made re-entrant with
            ``nest_asyncio.apply()``. Await the event there instead::

                await event.list()
        """
        loop = get_event_loop()

        if loop.is_running() and not getattr(loop, "_nest_patched", False):
            raise RuntimeError(
                "Event.run() cannot be called from within an already running "
                "asyncio event loop. Use 'await event.list()' instead."
            )

        # Subscribe to the collector before attaching it, and before the
        # loop runs: an event that has already ended makes it emit at once,
        # and a source can end on its very first step (an empty sequence).
        result = loop.create_future()

        def on_values(values):
            if not result.done():
                result.set_result(values)

        def on_error(_source, error):
            if not result.done():
                result.set_exception(error)

        collector = ListOp()
        collector.connect(on_values, on_error, keep_ref=True)
        collector.set_source(self)
        # The events of this pipeline, found now while every upstream link is
        # still connected (operators cut their links when they end).
        nodes = _pipeline_nodes([collector])
        owned = _pipeline_tasks(nodes)

        async def _run_and_clean():
            try:
                return await result
            finally:
                # The pipeline may have ended before some of its parts did
                # (e.g. ``timeout``, ``take``): stop what is left of it, and
                # only that, so unrelated work on the loop keeps running.
                # Tasks started during the run are found on the same events
                # and on events linked to them since (an emap's nested ones).
                current = _pipeline_tasks(_pipeline_nodes(nodes.values()))
                leftover = [t for t in owned | current if not t.done()]
                for task in leftover:
                    task.cancel()
                await asyncio.gather(*leftover, return_exceptions=True)

        return loop.run_until_complete(_run_and_clean())

    def pipe(self, *targets: Event):
        """
        Form several events into a pipe::

            import eventkit as ev

            e1 = ev.Sequence('abcde')
            e2 = ev.Enumerate().map(lambda i, c: (i, i + ord(c)))
            e3 = ev.Star().pluck(1).map(chr)

            e1.pipe(e2, e3)     # or: ev.Event.Pipe(e1, e2, e3)
            ->
            ['a', 'c', 'e', 'g', 'i']

        Args:
            targets: One or more Events that have no source yet,
                or ``Event`` constructors that needs no arguments.
        """
        source = self
        for t in targets:
            t = Event.create(t)
            t.set_source(source)
            source = t

        return source

    def fork(self, *targets: Event) -> Fork:
        """
        Fork this event into one or more target events.
        Square brackets can be used as a synonym::

            import eventkit as ev

            ev.Range(2, 5)[ev.Min, ev.Max, ev.Sum].zip()
            ->
            [(2, 2, 2), (2, 3, 5), (2, 4, 9)]

        The events in the fork can be combined by one of the join
        methods of ``Fork``.

        Args:
            targets: One or more events that have no source yet,
                or ``Event`` constructors that need no arguments.
        """
        fork = Fork()
        for t in targets:
            t = Event.create(t)
            t.set_source(self)
            fork.append(t)

        return fork

    def set_source(self, source):
        self._source = source

    def _onFinalize(self, ref):
        self._slots.remove_ref(ref)

    @staticmethod
    def _split(c):
        """
        Split given callable in (object, function) tuple.
        """
        if isinstance(c, types.FunctionType):
            return (None, c)

        if isinstance(c, functools.partial):
            # A partial is held like a function. The connecting code
            # created it, so the event is its only owner. PyPy partials
            # support weak references and CPython partials do not. The
            # generic rule below would hold a partial weakly on PyPy and
            # strongly on CPython.
            return (None, c)

        if isinstance(c, types.MethodType):
            return (c.__self__, c.__func__)

        if isinstance(c, types.BuiltinMethodType):
            owner = c.__self__
            if owner is not None and not isinstance(owner, types.ModuleType):
                # A built-in method bound to an object, e.g. ``a_list.append``:
                # each access makes a new bound method, so split it into the
                # object and the class's method descriptor, which has a
                # stable identity (needed to disconnect it again).
                descriptor = getattr(type(owner), c.__name__, None)
                if isinstance(descriptor, types.MethodDescriptorType):
                    return (owner, descriptor)
            # a built-in function such as print
            return (None, c)

        if hasattr(c, "__call__"):
            return (c, None)

        raise ValueError(f"Invalid callable: {c}")

    async def aiter(self, skip_to_last: bool = False, tuples: bool = False):
        """
        Create an asynchronous iterator that yields the emitted values
        from this event::

            async def coro():
                async for args in event.aiter():
                    ...

        :meth:`__aiter__` is a synonym for :meth:`aiter` with
        default arguments,

        Args:
            skip_to_last:
                * ``True``: Backlogged source values are skipped over to
                  yield only the latest value. Can be used as a
                  slipper clutch between a source that produces too fast
                  and the handling that can't keep up.
                * ``False``: All events are yielded.
            tuples:
                * ``True``: Always yield arguments as a tuple.
                * ``False``: Unpack single argument tuples.
        """

        def on_event(*args):
            if skip_to_last:
                while q.qsize():
                    q.get_nowait()
            q.put_nowait(("", args))

        def on_error(source, error):
            q.put_nowait(("ERROR", error))

        def on_done(source):
            q.put_nowait(("DONE", None))

        if self.done():
            return

        q: asyncio.Queue[tuple[str, AnyType]] = asyncio.Queue()
        self.connect(on_event, on_error, on_done)
        try:
            while True:
                what, args = await q.get()
                if not what:
                    yield (
                        args
                        if tuples
                        else args[0]
                        if len(args) == 1
                        else args or NO_VALUE
                    )
                elif what == "ERROR":
                    raise args
                else:
                    break
        finally:
            self.disconnect(on_event, on_error, on_done)

    __iadd__ = connect
    __isub__ = disconnect
    __call__ = emit
    __or__ = pipe

    def __repr__(self):
        return f"Event<{self.name()}, {self._slots}>"

    def __len__(self):
        return self._slots.count

    def __bool__(self):
        return True

    def __getitem__(self, fork_targets) -> Fork:
        if not hasattr(fork_targets, "__iter__"):
            fork_targets = (fork_targets,)

        return self.fork(*fork_targets)

    def __await__(self):
        """
        Asynchronously await the next emit of an event::

            async def coro():
                args = await event
                ...

        If the event does an empty ``emit()``, then the value
        of ``args`` is set to ``util.NO_VALUE``.

        :meth:`wait` and :meth:`__await__` are each other's inverse.
        """

        def on_event(*args):
            if not fut.done():
                fut.set_result(args[0] if len(args) == 1 else args or NO_VALUE)

        def on_error(source, error):
            if not fut.done():
                fut.set_exception(error)

        def on_future_done(f):
            self.disconnect(on_event, on_error)

        if self.done():
            raise ValueError("Event already done")

        fut: asyncio.Future[AnyType] = asyncio.get_running_loop().create_future()
        self.connect(on_event, on_error)
        fut.add_done_callback(on_future_done)
        return fut.__await__()

    __aiter__ = aiter
    """
    Synonym for :meth:`aiter` with default arguments::

        async def coro():
            async for args in event:
                ...

    :meth:`aiterate` and :meth:`__aiter__` are each other's inverse.
    """

    def __contains__(self, c):
        """
        See if callable is already connected.
        """
        obj, func = self._split(c)
        return self._slots.exists(obj, func)

    def __reduce__(self):
        """
        Pickle as a new event of the same name: listeners are not pickled.
        """
        return self.__class__, (self._name,)

    @staticmethod
    def init(obj, event_names: Iterable):
        """
        Convenience function for initializing multiple events as members
        of the given object.

        Args:
            event_names: Names to use for the created events.
        """
        for name in event_names:
            setattr(obj, name, Event(name))

    # dot access to constructors

    @staticmethod
    def create(obj):
        """
        Create an event from a async iterator, awaitable, or event
        constructor without arguments.

        Args:
            obj: The source object. If it's already an event then it
              is passed as-is.
        """
        if isinstance(obj, Event):
            return obj

        if hasattr(obj, "__call__"):
            obj = obj()

        if isinstance(obj, Event):
            return obj

        if hasattr(obj, "__aiter__"):
            return Event.aiterate(obj)

        if hasattr(obj, "__await__"):
            return Event.wait(obj)

        raise ValueError(f"Invalid type: {obj}")

    @staticmethod
    def wait(future: Awaitable) -> Wait:
        """
        Create a new event that emits the value of the
        awaitable when it becomes available and then set this event done.

        :meth:`wait` and :meth:`__await__` are each other's inverse.

        Args:
            future: Future to wait on.
        """
        return Wait(future)

    @staticmethod
    def aiterate(ait: AsyncIterable) -> Aiterate:
        """
        Create a new event that emits the yielded values from the
        asynchronous iterator.

        The asynchronous iterator serves as a source for both the time
        and value of emits.

        :meth:`aiterate` and :meth:`__aiter__` are each other's inverse.

        Args:
            ait: The asynchronous source iterator. It must ``await``
                at least once; If necessary use::

                    await asyncio.sleep(0)
        """
        return Aiterate(ait)

    @staticmethod
    def sequence(
        values: Iterable,
        interval: float = 0,
        times: Iterable[float] | None = None,
    ) -> Sequence:
        """
        Create a new event that emits the given values.
        Supply at most one ``interval`` or ``times``.

        Args:
            values: The source values.
            interval: Time interval in seconds between values.
            times: Relative times for individual values, in seconds since
                start of event. The sequence should match ``values``.
        """
        return Sequence(values, interval, times)

    @staticmethod
    def repeat(
        value=NO_VALUE,
        count=1,
        interval: float = 0,
        times: Iterable[float] | None = None,
    ) -> Repeat:
        """
        Create a new event that repeats ``value`` a number of ``count`` times.

        Args:
            value: The value to emit.
            count: Number of times to emit.
            interval: Time interval in seconds between values.
            times: Relative times for individual values, in seconds since
                start of event. The sequence should match ``values``.
        """
        return Repeat(value=value, count=count, interval=interval, times=times)

    @staticmethod
    def range(
        *args, interval: float = 0, times: Iterable[float] | None = None
    ) -> Range:
        """
        Create a new event that emits the values from a range.

        Args:
            args: Same as for built-in ``range``.
            interval: Time interval in seconds between values.
            times: Relative times for individual values, in seconds since
                start of event. The sequence should match the range.
        """
        return Range(*args, interval=interval, times=times)

    @staticmethod
    def timerange(start=0, end=None, step=1) -> Timerange:
        """
        Create a new event that emits the datetime value, at that datetime,
        from a range of datetimes.

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
        return Timerange(start, end, step)

    @staticmethod
    def timer(interval: float, count: int | None = None) -> Timer:
        """
        Create a new timer event that emits at regularly paced intervals
        the number of seconds since starting it.

        Args:
            interval: Time interval in seconds between emits.
            count: Number of times to emit, or ``None`` for no limit.
        """
        return Timer(interval, count)

    @staticmethod
    def marble(
        s: str, interval: float = 0, times: Iterable[float] | None = None
    ) -> Marble:
        """
        Create a new event that emits the values from a Rx-type marble string.

        Args:
            s: The string with characters that are emitted.
            interval: Time interval in seconds between values.
            times: Relative times for individual values, in seconds since
                start of event. The sequence should match the marble string.

        """
        return Marble(s, interval, times)

    # dot access to operators

    def filter(self, predicate=bool) -> Filter:
        """
        For every source value, apply predicate and re-emit when True.

        Args:
            predicate: The function to test every source value with.
                The default is to test the general truthiness with ``bool()``.
        """
        return Filter(predicate, self)

    def skip(self, count: int = 1) -> Skip:
        """
        Drop the first ``count`` values from source and follow the source
        after that.

        Args:
            count: Number of source values to drop.
        """
        return Skip(count, self)

    def take(self, count: int = 1) -> Take:
        """
        Re-emit first ``count`` values from the source and then end.

        Args:
            count: Number of source values to re-emit.
        """
        return Take(count, self)

    def takewhile(self, predicate=bool) -> TakeWhile:
        """
        Re-emit values from the source until the predicate becomes False
        and then end.

        Args:
            predicate: The function to test every source value with.
                The default is to test the general truthiness with ``bool()``.
        """
        return TakeWhile(predicate, self)

    def dropwhile(self, predicate=lambda x: not x) -> DropWhile:
        """
        Drop source values until the predicate becomes False and after that
        re-emit everything from the source.

        Args:
            predicate: The function to test every source value with.
                The default is to test the inverted general truthiness.
        """
        return DropWhile(predicate, self)

    def takeuntil(self, notifier: Event) -> TakeUntil:
        """
        Re-emit values from the source until the ``notifier`` emits
        and then end. If the notifier ends without any emit then
        keep passing source values.

        Args:
            notifier: Event that signals to end this event.
        """
        return TakeUntil(notifier, self)

    def constant(self, constant) -> Constant:
        """
        On emit of the source emit a constant value::

            emit(value) -> emit(constant)

        Args:
            constant: The constant value to emit.
        """
        return Constant(constant, self)

    def iterate(self, it) -> Iterate:
        """
        On emit of the source, emit the next value from an iterator::

            emit(a, b, ...) -> emit(next(it))

        The time of events follows the source and the values follow
        the iterator.

        Args:
            it: The source iterator to use for generating values. When the
                iterator is exhausted the event is set to be done.
        """
        return Iterate(it, self)

    def count(self, start=0, step=1) -> Count:
        """
        Count and emit the number of source emits::

            emit(a, b, ...) -> emit(count)

        Args:
            start: Start count.
            step: Add count by this amount for every new source value.
        """
        return Count(start, step, self)

    def enumerate(self, start=0, step=1) -> Enumerate:
        """
        Add a count to every source value::

            emit(a, b, ...) -> emit(count, a, b, ...)

        Args:
            start: Start count.
            step: Increase by this amount for every new source value.
        """
        return Enumerate(start, step, self)

    def timestamp(self) -> Timestamp:
        """
        Add a timestamp (from time.time()) to every source value::

            emit(a, b, ...) -> emit(timestamp, a, b, ...)

        The timestamp is the float number in seconds since the
        midnight Jan 1, 1970 epoch.
        """
        return Timestamp(self)

    def partial(self, *left_args) -> Partial:
        """
        Pad source values with extra arguments on the left::

            emit(a, b, ...) -> emit(*left_args, a, b, ...)

        Args:
            left_args: Arguments to inject.
        """
        return Partial(*left_args, source=self)

    def partial_right(self, *right_args) -> PartialRight:
        """
        Pad source values with extra arguments on the right::

            emit(a, b, ...) -> emit(a, b, ..., *right_args)

        Args:
            right_args: Arguments to inject.
        """
        return PartialRight(*right_args, source=self)

    def star(self) -> Star:
        """
        Unpack a source tuple into positional arguments, similar to the
        star operator::

            emit((a, b, ...)) -> emit(a, b, ...)

        :meth:`star` and :meth:`pack` are each other's inverse.
        """
        return Star(self)

    def pack(self) -> Pack:
        """
        Pack positional arguments into a tuple::

            emit(a, b, ...) -> emit((a, b, ...))

        :meth:`star` and :meth:`pack` are each other's inverse.
        """
        return Pack(self)

    def pluck(self, *selections: int | str) -> Pluck:
        """
        Extract arguments or nested properties from the source values.

        Select which argument positions to keep::

            emit(a, b, c, d).pluck(1, 2) -> emit(b, c)

        Re-order arguments::

            emit(a, b, c).pluck(2, 1, 0) -> emit(c, b, a)

        To do an empty emit leave ``selections`` empty::

            emit(a, b).pluck() -> emit()

        Select nested properties from positional arguments::

            emit(person, account).pluck(
                '1.number', '0.address.street') ->

            emit(account.number, person.address.street)

        If no value can be extracted then ``NO_VALUE`` is emitted in its place.

        Args:
            selections: The values to extract.
        """
        return Pluck(*selections, source=self)

    def map(self, func, timeout=None, ordered=True, task_limit=None) -> Map:
        """
        Apply a sync or async function to source values using
        positional arguments::

            emit(a, b, ...) -> emit(func(a, b, ...))

        or if ``func`` returns an awaitable then it will be awaited::

            emit(a, b, ...) -> emit(await func(a, b, ...))

        When ``func`` raises (or its awaitable fails or times out), the error
        is emitted on ``error_event`` as ``(this event, exception)`` and no
        value is emitted.

        Args:
            func: The function or coroutine constructor to apply.
            timeout: Timeout in seconds since coroutine is started
            ordered:
                * ``True``: The order of emitted results preserves the
                  order of the source values.
                * ``False``: Results are in order of completion.
            task_limit: Max number of concurrent tasks, or None for no limit.

        ``timeout``, ``ordered`` and ``task_limit`` apply to
        async functions only. To run ``func`` in other threads or
        processes, see :meth:`poolmap`.
        """
        return Map(func, timeout, ordered, task_limit, source=self)

    def poolmap(
        self,
        executor: Executor,
        func,
        *,
        timeout: float | None = None,
        chunksize: int = 1,
        ordered: bool = True,
    ) -> PoolMap:
        """
        Apply ``func`` to source values in a :mod:`concurrent.futures`
        executor, so the work runs in parallel outside the event loop::

            emit(a, b, ...) -> emit(func(a, b, ...))  # run by the executor

        ``executor`` is any :class:`concurrent.futures.Executor`:

        * :class:`~concurrent.futures.ProcessPoolExecutor`: CPU-bound work
          on all cores (``func`` and the values must be picklable, so
          ``func`` is a module-level function, not a lambda);
        * :class:`~concurrent.futures.InterpreterPoolExecutor` (Python
          3.14+): CPU-bound work in parallel subinterpreters;
        * :class:`~concurrent.futures.ThreadPoolExecutor`: blocking I/O,
          or CPU-bound work on a free-threaded Python build;
        * any other executor with the same interface.

        The executor belongs to the caller, who shuts it down; ``poolmap``
        only submits to it::

            import bz2
            from concurrent.futures import ProcessPoolExecutor

            with ProcessPoolExecutor() as pool:
                sizes = (
                    ev.Sequence(data).poolmap(pool, bz2.compress)
                    .map(len).list().run()
                )

        As with :meth:`map`, an exception raised by ``func`` for a value is
        emitted on ``error_event`` as ``(this event, exception)`` with no
        value, and the other values are unaffected.

        Args:
            executor: The executor that runs ``func``.
            func: A plain (not async) function.
            timeout: Seconds a submitted chunk may take; when it is
                exceeded, ``asyncio.TimeoutError`` is emitted as the error
                of that chunk, and the chunk is cancelled if it has not
                started yet. ``None``: no limit.
            chunksize: Number of source values sent to the executor in one
                submission. Larger chunks cut the per-call overhead of small
                tasks (above all with processes); values are held until
                their chunk is full or the source ends.
            ordered:
                * ``True``: results in the order of the source values;
                * ``False``: results of each chunk as soon as it completes.
        """
        return PoolMap(executor, func, timeout, chunksize, ordered, source=self)

    def emap(self, constr, joiner: AddableJoinOp) -> Emap:
        """
        Higher-order event map that creates a new ``Event`` instance
        for every source value::

            emit(a, b, ...) -> new Event constr(a, b, ...)

        Args:
            constr: Constructor function for creating a new event.
                Apart from returning  an ``Event``, the constructor may also
                return an awaitable or an asynchronous iterator, in which
                case an ``Event`` will be created.
            joiner: Join operator to combine the emits of nested events.
        """
        return Emap(constr, joiner, self)

    def mergemap(self, constr) -> Mergemap:
        """
        :meth:`emap` that uses :meth:`merge` to combine the nested events::

            marbles = [
                'A   B    C    D',
                '_1   2  3    4',
                '__K   L     M   N']

            ev.Range(3, interval=1).mergemap(
                lambda v: ev.Marble(marbles[v], interval=1))
            ->
            ['A', '1', 'K', 'B', '2', 'L', '3', 'C', 'M', '4', 'D', 'N']
        """
        return Mergemap(constr, self)

    def concatmap(self, constr) -> Concatmap:
        """
        :meth:`emap` that uses :meth:`concat` to combine the nested events:
        each nested event is followed until it ends, then the next one;
        what a later one emits before its turn is dropped (``1``, ``2``
        and ``K`` here)::

            marbles = [
                'A    B    C    D',
                '_       1    2    3    4',
                '__                  K    L      M   N']

            ev.Range(3, interval=1).concatmap(
                lambda v: ev.Marble(marbles[v], interval=1))
            ->
            ['A', 'B', 'C', 'D', '3', '4', 'L', 'M', 'N']
        """
        return Concatmap(constr, self)

    def chainmap(self, constr) -> Chainmap:
        """
        :meth:`emap` that uses :meth:`chain` to combine the nested events::

            marbles = [
                'A    B    C    D           ',
                '_       1    2    3    4',
                '__                  K    L      M   N']

            ev.Range(3, interval=1).chainmap(
                lambda v: ev.Marble(marbles[v], interval=1))
            ->
            ['A', 'B', 'C', 'D', '1', '2', '3', '4', 'K', 'L', 'M', 'N']
        """
        return Chainmap(constr, self)

    def switchmap(self, constr) -> Switchmap:
        """
        :meth:`emap` that uses :meth:`switch` to combine the nested events::

            marbles = [
                'A    B    C    D           ',
                '_                 K    L      M   N',
                '__      1    2      3    4'
            ]
            ev.Range(3, interval=1).switchmap(
                lambda v: ev.Marble(marbles[v], interval=1))
            ->
            ['A', 'B', '1', '2', 'K', 'L', 'M', 'N']
        """
        return Switchmap(constr, self)

    def reduce(self, func, initializer=NO_VALUE) -> Reduce:
        """
        Apply a two-argument reduction function to the previous reduction
        result and the current value and emit the new reduction result.

        Args:
            func: Reduction function::

                emit(args) -> emit(func(prev_args, args))

            initializer: First argument of first reduction::

                    first_result = func(initializer, first_value)

                If no initializer is given, then the first result is
                emitted on the second source emit.
        """
        return Reduce(func, initializer, self)

    def min(self) -> Min:
        """
        Minimum value.
        """
        return Min(self)

    def max(self) -> Max:
        """
        Maximum value.
        """
        return Max(self)

    def sum(self, start=0) -> Sum:
        """
        Total sum.

        Args:
            start: Value added to total sum.
        """
        return Sum(start, self)

    def product(self, start=1) -> Product:
        """
        Total product.

        Args:
            start: Initial start value.
        """
        return Product(start, self)

    def mean(self) -> Mean:
        """
        Total average.
        """
        return Mean(self)

    def any(self) -> Any:
        """
        Test if predicate holds for at least one source value.
        """
        return Any(self)

    def all(self) -> All:
        """
        Test if predicate holds for all source values.
        """
        return All(self)

    def ema(self, n: int | None = None, weight: float | None = None) -> Ema:
        """
        Exponential moving average.

        Args:
            n: Number of periods.
            weight: Weight of new value.

        Give either ``n`` or ``weight``.
        The relation is ``weight = 2 / (n + 1)``.
        """
        return Ema(n, weight, self)

    def previous(self, count: int = 1) -> Previous:
        """
        For every source value, emit the ``count``-th previous value::

            source:  -ab---c--d-e-
            output:  --a---b--c-d-

        Starts emitting on the ``count + 1``-th source emit.

        Args:
            count: Number of periods to go back.
        """
        return Previous(count, self)

    def pairwise(self) -> Pairwise:
        """
        Emit ``(previous_source_value, current_source_value)`` tuples.
        Starts emitting on the second source emit::

            source:  -a----b------c--------d-----
            output:  ------(a,b)--(b,c)----(c,d)-
        """
        return Pairwise(self)

    def changes(self) -> Changes:
        """
        Emit only source values that have changed from the previous value.
        """
        return Changes(self)

    def unique(self, key=None) -> Unique:
        """
        Emit only unique values, dropping values that have already
        been emitted.

        Args:
            key: `The callable `'key(value)`` is used to group values.
                The default of ``None`` groups values by equality.
                The resulting group must be hashable.
        """
        return Unique(key, self)

    def last(self) -> Last:
        """
        Wait until source has ended and re-emit its last value.
        """
        return Last(self)

    def list(self) -> ListOp:
        """
        Collect all source values and emit as list when the source ends.
        """
        return ListOp(self)

    def deque(self, count=0) -> Deque:
        """
        Emit a ``deque`` with the last ``count`` values from the source
        (or less in the lead-in phase).

        Args:
            count: Number of last periods to use, or 0 to use all.
        """
        return Deque(count, self)

    def array(self, count=0) -> Array:
        """
        Emit a numpy array with the last ``count`` values from the source
        (or less in the lead-in phase).

        Args:
            count: Number of last periods to use, or 0 to use all.
        """
        return Array(count, self)

    def chunk(self, size: int) -> Chunk:
        """
        Chunk values up in lists of equal size. The last chunk can be shorter.

        Args:
            size: Chunk size.
        """
        return Chunk(size, self)

    def chunkwith(self, timer: Event, emit_empty: bool = True) -> ChunkWith:
        """
        Emit a chunked list of values when the timer emits.

        Args:
            timer: Event to use for timing the chunks.
            emit_empty: Emit empty list if no values present since last emit.
        """
        return ChunkWith(timer, emit_empty, self)

    def chain(self, *sources: Event) -> Chain:
        """
        Re-emit from a source until it ends, then move to the next source,
        Repeat until all sources have ended, ending the chain.
        Emits from pending sources are queued up::

            source 1:  -a----b---c|
            source 2:        --2-----3--4|
            source 3:  ------------x---------y--|
            output:    -a----b---c2--3--4x---y--|


        Args:
            sources: Source events.
        """
        return Chain(self, *sources)

    def merge(self, *sources) -> Merge:
        """
        Re-emit everything from the source events::

            source 1:  -a----b-------------c------d-|
            source 2:     ------1-----2------3--4-|
            source 3:      --------x----y--|
            output:    -a----b--1--x--2-y--c-3--4-d-|

        Args:
            sources: Source events.
        """
        return Merge(self, *sources)

    def concat(self, *sources) -> Concat:
        """
        Re-emit everything from one source until it ends and then move
        to the next source::

            source 1:  -a----b-----|
            source 2:    --1-----2-----3----4--|
            source 3:                 -----------x--y--|
            output:    -a----b---------3----4----x--y--|

        Args:
            sources: Source events.
        """
        return Concat(self, *sources)

    def switch(self, *sources) -> Switch:
        """
        Re-emit everything from one source and move to another source as soon
        as that other source starts to emit::

            source 1:  -a----b---c-----d---|
            source 2:        -----------x---y-|
            source 3:  ---------1----2----3-----|
            output:    -a----b--1----2--x---y---|

        Args:
            sources: Source events.
        """
        return Switch(self, *sources)

    def zip(self, *sources) -> Zip:
        """
        Zip sources together: The i-th emit has the i-th value from
        each source as positional arguments. Only emits when each source has
        emitted its i-th value, and ends as soon as a source has ended with
        no value left to pair (no further tuple is possible)::

            source 1:    -a----b------------------c------d---e--f---|
            source 2:    --------1-------2-------3---------4-----|
            output emit: --------(a,1)---(b,2)----(c,3)----(d,4)-|


        Args:
            sources: Source events.
        """
        return Zip(self, *sources)

    def ziplatest(self, *sources, partial: bool = True) -> Ziplatest:
        """
        Emit zipped values with the latest value from each of the
        source events. Emits every time when a source emits::

            source 1:   -a-------------------b-------c---|
            source 2:   ---------------1--------------------2------|
            output emit: (a,NoValue)---(a,1)-(b,1)---(c,1)--(c,2)--|

        Args:
            sources: Source events.
            partial:
                * True: Use ``NoValue`` for sources that have not emitted yet.
                * False: Wait until all sources have emitted.
        """
        return Ziplatest(self, *sources, partial=partial)

    def delay(self, delay) -> Delay:
        """
        Time-shift all source events by a delay::

            source:  -abc-d-e---f---|
            output:  ---abc-d-e---f---|

        This applies to the source errors and the source done event as well.

        Args:
            delay: Time delay of all events (in seconds).
        """
        return Delay(delay, self)

    def timeout(self, timeout) -> Timeout:
        """
        When the source doesn't emit for longer than the timeout period,
        do an empty emit and set this event as done.

        Args:
            timeout: Timeout value.
        """
        return Timeout(timeout, self)

    def throttle(
        self, maximum, interval, cost_func=None, *, inclusive: bool = False
    ) -> Throttle:
        """
        Limit number of emits per time without dropping values.
        Values that come in too fast are queued and re-emitted as soon
        as allowed by the limits.

        A nested ``status_event`` emits ``True`` when throttling starts
        and ``False`` when throttling ends.

        The limit can be dynamically changed with ``set_limit``.

        Args:
            maximum: Maximum payload per interval.
            interval: Time interval (in seconds).
            cost_func: The cost of a value; the sum of the costs of the
                values emitted inside any ``interval`` is limited by
                ``maximum``. The default is to count every value as 1.
            inclusive: ``False`` (the default) keeps that sum below the
                ``maximum``, so ``throttle(10, 1)`` emits at most 9 values
                per second; ``True`` lets it reach the ``maximum``, so
                ``throttle(10, 1, inclusive=True)`` emits up to 10.

        Either way, a value whose cost alone exceeds the limit is emitted on
        its own, once the interval holds no other emits.
        """
        return Throttle(maximum, interval, cost_func, self, inclusive=inclusive)

    def debounce(self, delay, on_first: bool = False) -> Debounce:
        """
        Filter out values from the source that happen in rapid succession.

        Args:
            delay: Maximal time difference (in seconds) between
                successive values before debouncing kicks in.
            on_first:
                * True: First value is send immediately and following values
                  in the rapid succession are dropped::

                    source: -abcd----efg-
                    output: -a-------e---

                * False: Last value of a rapid succession is send after
                  the delay and the values before that are dropped::

                    source:  -abcd----efg--
                    output:   ----d------g-
        """
        return Debounce(delay, on_first, self)

    def copy(self) -> Copy:
        """
        Create a shallow copy of the source values.
        """
        return Copy(self)

    def deepcopy(self) -> Deepcopy:
        """
        Create a deep copy of the source values.
        """
        return Deepcopy(self)

    def sample(self, timer: Event) -> Sample:
        """
        At the times that the timer emits, sample the value from this
        event and emit the sample.

        Args:
            timer: Event used to time the samples.
        """
        return Sample(timer, self)

    def errors(self) -> Errors:
        """
        Emit errors from the source.
        """
        return Errors(self)

    def end_on_error(self) -> EndOnError:
        """
        End on any error from the source.
        """
        return EndOnError(self)


from .ops.aggregate import (
    All,
    Any,
    Count,
    Deque,
    Ema,
    Max,
    Mean,
    Min,
    Pairwise,
    Product,
    Reduce,
    Sum,
)
from .ops.aggregate import List as ListOp
from .ops.array import (
    Array,
)
from .ops.combine import (
    AddableJoinOp,
    Chain,
    Concat,
    Fork,
    Merge,
    Switch,
    Zip,
    Ziplatest,
)
from .ops.create import (
    Aiterate,
    Marble,
    Range,
    Repeat,
    Sequence,
    Timer,
    Timerange,
    Wait,
)
from .ops.misc import EndOnError, Errors
from .ops.op import Op
from .ops.select import (
    Changes,
    DropWhile,
    Filter,
    Last,
    Skip,
    Take,
    TakeUntil,
    TakeWhile,
    Unique,
)
from .ops.timing import Debounce, Delay, Sample, Throttle, Timeout
from .ops.transform import (
    Chainmap,
    Chunk,
    ChunkWith,
    Concatmap,
    Constant,
    Copy,
    Deepcopy,
    Emap,
    Enumerate,
    Iterate,
    Map,
    Mergemap,
    Pack,
    Partial,
    PartialRight,
    Pluck,
    PoolMap,
    Previous,
    Star,
    Switchmap,
    Timestamp,
)
