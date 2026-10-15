from __future__ import annotations

import functools
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..event import Event, event_class
from ..util import NO_VALUE
from .op import Op


@dataclass(slots=True, eq=False, repr=False, init=False)
class Fork(list):
    """The targets of :meth:`Event.fork`, to be joined into one event."""

    def __init__(self):
        list.__init__(self)

    def join(self, joiner: JoinOp):
        joiner._set_sources(*self)
        self.clear()
        return joiner

    def concat(self) -> Concat:
        return self.join(Concat())

    def merge(self) -> Merge:
        return self.join(Merge())

    def switch(self) -> Switch:
        return self.join(Switch())

    def zip(self) -> Zip:
        return self.join(Zip())

    def ziplatest(self) -> Ziplatest:
        return self.join(Ziplatest())

    def chain(self) -> Chain:
        return self.join(Chain())


@event_class
class JoinOp(Op):
    """
    Base class for join operators that combine the emits
    from multiple source events.
    """

    _sources: deque[Event]

    def _set_sources(self, sources):
        raise NotImplementedError


@event_class
class AddableJoinOp(JoinOp):
    """
    Base class for join operators where new sources, produced by a
    parent higher-order event, can be added dynamically.
    """

    _parent: Event | None

    def __init__(self, *sources: Event):
        JoinOp.__init__(self)
        self._sources = deque()
        self._parent = None
        self._set_sources(*sources)

    def _set_sources(self, *sources):
        if not sources:
            return  # sources (or a parent) come later
        for source in sources:
            self.add_source(Event.create(source))
        self._end_if_exhausted()

    def add_source(self, source):
        """Follow ``source`` as well. A source that has already ended has
        nothing more to give and is skipped. The same source can be added
        more than once."""
        if not source.done():
            self._add_source(source)

    def _add_source(self, source):
        raise NotImplementedError

    def set_parent(self, parent: Event):
        if parent.done():
            self._parent = None
            self._end_if_exhausted()
            return
        self._parent = parent
        parent.done_event += self._on_parent_done

    def on_source_done(self, source):
        self._disconnect_from(source)
        self._sources.remove(source)
        self._end_if_exhausted()

    def _on_parent_done(self, parent):
        parent.done_event -= self._on_parent_done
        self._parent = None
        self._end_if_exhausted()

    def _end_if_exhausted(self):
        """End once no source is left and no parent can add more."""
        if not self._sources and self._parent is None:
            self.set_done()


@event_class
class Merge(AddableJoinOp):
    def _add_source(self, source):
        self._sources.append(source)
        self._connect_from(source)


@event_class
class Switch(AddableJoinOp):
    _source2cb: dict[Event, Callable[..., None]]
    _active_source: Event | None

    def __init__(self, *sources):
        AddableJoinOp.__init__(self)
        self._source2cb = {}  # map from source to callback
        self._active_source = None
        self._set_sources(*sources)

    def _add_source(self, source):
        self._sources.append(source)
        cb = self._source2cb.get(source)
        if not cb:
            cb = functools.partial(self.on_source_s, source)
            self._source2cb[source] = cb
            source.connect(cb, self.on_source_error, self.on_source_done)

    def _remove_source(self, source):
        if source in self._sources:
            # Every entry: a source added more than once shares one callback.
            while source in self._sources:
                self._sources.remove(source)
            cb = self._source2cb.pop(source, None)
            if cb:
                # All three: a dropped source must not end this event later.
                source.disconnect(cb, self.on_source_error, self.on_source_done)

    def on_source_s(self, source, *args):
        if source is not self._active_source:
            self._remove_source(self._active_source)
            self._active_source = source
        self.emit(*args)

    def on_source_done(self, source):
        self._remove_source(source)
        if not self._sources and self._parent is None:
            self._active_source = None
            self.set_done()


@event_class
class Concat(AddableJoinOp):
    """Follow the sources one at a time, in order: the first until it
    ends, then the next. A later source's values are dropped while an
    earlier one is still running (``chain`` queues them instead); a later
    source that ends before its turn is skipped."""

    _source2cb: dict[Event, Callable[..., None]]

    def __init__(self, *sources):
        AddableJoinOp.__init__(self)
        self._source2cb = {}  # map from source to callback
        self._set_sources(*sources)

    def _add_source(self, source):
        if source in self._sources:
            return  # already followed in its turn
        self._sources.append(source)
        cb = functools.partial(self._on_source_s, source)
        self._source2cb[source] = cb
        # Every source is watched from the start: its errors are passed on
        # right away, and its end is noticed even before its turn.
        source.connect(cb, self.on_source_error, self.on_source_done)

    def _on_source_s(self, source, *args):
        if self._sources[0] is source:
            self.emit(*args)

    def on_source_done(self, source):
        cb = self._source2cb.pop(source, None)
        if cb is not None:
            source.disconnect(cb, self.on_source_error, self.on_source_done)
        while source in self._sources:
            self._sources.remove(source)
        self._end_if_exhausted()


@event_class
class Chain(AddableJoinOp):
    _qq: deque[deque[tuple[Any, ...]]]
    _source2cbs: defaultdict[Event, list[Callable[..., None]]]

    def __init__(self, *sources):
        AddableJoinOp.__init__(self)
        self._qq = deque()
        self._source2cbs = defaultdict(list)  # map from source to callbacks
        self._set_sources(*sources)

    def _add_source(self, source):
        if not self._sources:
            self._connect_from(source)
        else:
            q: deque[tuple[Any, ...]] = deque()

            def cb(*args):
                q.append(args)

            self._qq.append(q)
            # Values wait their turn; errors are passed on right away.
            source.connect(cb, self.on_source_error)
            self._source2cbs[source].append(cb)
        self._sources.append(source)

    def on_source_done(self, source):
        if source is not self._sources[0]:
            return
        self._disconnect_from(source)
        self._sources.popleft()
        while self._sources:
            source = self._sources[0]
            q = self._qq.popleft()
            for args in q:
                self.emit(*args)
            for cb in self._source2cbs.pop(source, []):
                source.disconnect(cb, self.on_source_error)
            if source.done():
                self._sources.popleft()
                continue
            self._connect_from(source)
            return
        self._end_if_exhausted()


@event_class
class Zip(JoinOp):
    _inputs: list[Event]  # the sources by position (one may appear twice)
    _results: list[deque[Any]]
    _ended: set[int]  # positions whose source has ended
    _source2cbs: defaultdict[Event, list[Callable[..., None]]]
    _num_ready: int

    def __init__(self, *sources):
        JoinOp.__init__(self)
        self._num_ready = 0  # number of sources with a pending result
        self._source2cbs = defaultdict(list)  # map from source to callbacks
        self._ended = set()
        if sources:
            self._set_sources(*sources)

    def _set_sources(self, *sources):
        self._inputs = [Event.create(s) for s in sources]
        self._sources = deque(self._inputs)
        if any(s.done() for s in self._inputs):
            self.set_done()
            return
        self._results = [deque() for _ in self._inputs]
        for i, source in enumerate(self._inputs):
            cb = functools.partial(self._on_source_i, i)
            source.connect(cb, self.on_source_error, self.on_source_done)
            self._source2cbs[source].append(cb)

    def _on_source_i(self, i, *args):
        q = self._results[i]
        if not q:
            self._num_ready += 1
            ready = self._num_ready == len(self._results)
        else:
            ready = False
        q.append(args[0] if len(args) == 1 else args or NO_VALUE)
        if ready:
            tup = tuple(q.popleft() for q in self._results)
            self._num_ready = sum(bool(q) for q in self._results)
            self.emit(*tup)
            self._end_if_exhausted()

    def on_source_done(self, source):
        if source in self._sources:
            self._sources.remove(source)
        self._ended.update(i for i, s in enumerate(self._inputs) if s is source)
        self._end_if_exhausted()

    def _end_if_exhausted(self):
        """Ends as soon as an ended source has no value left to pair: no
        further tuple is possible."""
        if self._done or not any(not self._results[i] for i in self._ended):
            return
        for source, cbs in self._source2cbs.items():
            for cb in cbs:
                source.disconnect(cb, self.on_source_error, self.on_source_done)
        self._source2cbs.clear()
        self.set_done()


@event_class
class Ziplatest(JoinOp):
    _values: list[Any]
    _is_primed: bool
    _source2cbs: defaultdict[Event, list[Callable[..., None]]]

    def __init__(self, *sources, partial=True):
        JoinOp.__init__(self)
        self._is_primed = partial
        self._source2cbs = defaultdict(list)  # map from source to callbacks
        if sources:
            self._set_sources(*sources)

    def _set_sources(self, *sources):
        events = [Event.create(s) for s in sources]
        self._sources = deque(e for e in events if not e.done())
        if not self._sources:
            self.set_done()
            return
        # One position per source, finished ones included (they keep their
        # last value); callbacks index into these positions.
        self._values = [e.value() for e in events]
        for i, source in enumerate(events):
            if source.done():
                continue
            cb = functools.partial(self._on_source_i, i)
            source.connect(cb, self.on_source_error, self.on_source_done)
            self._source2cbs[source].append(cb)

    def _on_source_i(self, i, *args):
        self._values[i] = args[0] if len(args) == 1 else args or NO_VALUE
        if not self._is_primed:
            self._is_primed = not any(r is NO_VALUE for r in self._values)
        if self._is_primed:
            self.emit(*self._values)

    def on_source_done(self, source):
        self._sources.remove(source)
        if not self._sources:
            for source, cbs in self._source2cbs.items():
                for cb in cbs:
                    source.disconnect(cb, self.on_source_error, self.on_source_done)
            self._source2cbs.clear()
            self.set_done()
