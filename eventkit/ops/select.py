from __future__ import annotations

from collections.abc import Callable, Hashable
from typing import Any

from ..event import Event, event_class
from ..util import NO_VALUE
from .op import Op


@event_class
class Filter(Op):
    _predicate: Callable[..., Any]

    def __init__(self, predicate=bool, source=None):
        Op.__init__(self, source)
        self._predicate = predicate

    def on_source(self, *args):
        if self._predicate(*args):
            self.emit(*args)


@event_class
class Skip(Op):
    _count: int
    _n: int

    def __init__(self, count=1, source=None):
        Op.__init__(self, source)
        self._count = count
        self._n = 0

    def on_source(self, *args):
        # Stays subscribed through ``on_source`` (held strongly by the
        # source); re-subscribing ``emit`` would hold it only weakly.
        if self._n < self._count:
            self._n += 1
        else:
            self.emit(*args)


@event_class
class Take(Op):
    _count: int
    _n: int

    def __init__(self, count=1, source=None):
        Op.__init__(self)
        self._count = count
        self._n = 0
        if count <= 0:
            self.set_done()  # takes nothing: done at once, never attached
        elif source is not None:
            self.set_source(source)

    def on_source(self, *args):
        self._n += 1
        if self._n <= self._count:
            self.emit(*args)
        if self._n == self._count:
            self._detach()
            self.set_done()


@event_class
class TakeWhile(Op):
    _predicate: Callable[..., Any]

    def __init__(self, predicate=bool, source=None):
        Op.__init__(self, source)
        self._predicate = predicate

    def on_source(self, *args):
        if self._predicate(*args):
            self.emit(*args)
        else:
            self.set_done()
            self._detach()


@event_class
class DropWhile(Op):
    _predicate: Callable[..., Any]
    _drop: bool

    def __init__(self, predicate=lambda x: not x, source=None):
        Op.__init__(self, source)
        self._predicate = predicate
        self._drop = True

    def on_source(self, *args):
        if self._drop:
            self._drop = self._predicate(*args)
        if not self._drop:
            self.emit(*args)


@event_class
class TakeUntil(Op):
    _notifier: Event | None

    def __init__(self, notifier, source=None):
        Op.__init__(self)
        self._notifier = None
        # A notifier that has ended without emitting never stops the source.
        if not notifier.done():
            self._notifier = notifier
            notifier.connect(
                self._on_notifier, self.on_source_error, self._release_notifier
            )
        if source is not None:
            self.set_source(source)

    def _on_notifier(self, *args):
        self.on_source_done(self._source)

    def _release_notifier(self, *args):
        notifier = self._notifier
        if notifier is not None:
            notifier.disconnect(
                self._on_notifier, self.on_source_error, self._release_notifier
            )
            self._notifier = None

    def on_source_done(self, source):
        Op.on_source_done(self, self._source)
        self._release_notifier()


@event_class
class Changes(Op):
    _prev: Any

    def __init__(self, source=None):
        Op.__init__(self, source)
        self._prev = NO_VALUE

    def on_source(self, *args):
        if args != self._prev:
            self.emit(*args)
        self._prev = args


@event_class
class Unique(Op):
    _key: Callable[..., Hashable] | None
    _seen: set[Hashable]

    def __init__(self, key, source=None):
        Op.__init__(self, source)
        self._key = key
        self._seen = set()

    def on_source(self, *args):
        group = args if self._key is None else self._key(*args)
        if group not in self._seen:
            self._seen.add(group)
            self.emit(*args)


@event_class
class Last(Op):
    _last: Any

    def __init__(self, source=None):
        Op.__init__(self)
        self._last = NO_VALUE
        if source is not None:
            self.set_source(source)

    def on_source(self, *args):
        self._last = args

    def on_source_done(self, source):
        if self._last is not NO_VALUE:  # an empty source has no last value
            self.emit(*self._last)
        Op.on_source_done(self, source)
