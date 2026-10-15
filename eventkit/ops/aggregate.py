from __future__ import annotations

import itertools
import operator
from collections import deque
from collections.abc import Callable
from typing import Any as AnyType

from ..event import event_class
from ..util import NO_VALUE
from .op import Op
from .transform import Iterate


@event_class
class Count(Iterate):
    def __init__(self, start=0, step=1, source=None):
        it = itertools.count(start, step)
        Iterate.__init__(self, it, source)


@event_class
class Reduce(Op):
    _func: Callable[[AnyType, AnyType], AnyType]
    _initializer: AnyType
    _prev: AnyType

    def __init__(self, func, initializer=NO_VALUE, source=None):
        Op.__init__(self, source)
        self._func = func
        self._initializer = initializer
        self._prev = NO_VALUE

    def on_source(self, arg):
        if self._prev is NO_VALUE:
            if self._initializer is NO_VALUE:
                self._prev = arg
            else:
                self._prev = self._func(self._initializer, arg)
                self.emit(self._prev)
        else:
            self._prev = self._func(self._prev, arg)
            self.emit(self._prev)


@event_class
class Min(Reduce):
    def __init__(self, source=None):
        Reduce.__init__(self, min, float("inf"), source)


@event_class
class Max(Reduce):
    def __init__(self, source=None):
        Reduce.__init__(self, max, -float("inf"), source)


@event_class
class Sum(Reduce):
    def __init__(self, start=0, source=None):
        Reduce.__init__(self, operator.add, start, source)


@event_class
class Product(Reduce):
    def __init__(self, start=1, source=None):
        Reduce.__init__(self, operator.mul, start, source)


@event_class
class Mean(Op):
    _count: int
    _sum: float

    def __init__(self, source=None):
        Op.__init__(self, source)
        self._count = 0
        self._sum = 0

    def on_source(self, arg):
        self._count += 1
        self._sum += arg
        self.emit(self._sum / self._count)


@event_class
class Any(Reduce):
    def __init__(self, source=None):
        Reduce.__init__(self, lambda prev, v: prev or bool(v), False, source)


@event_class
class All(Reduce):
    def __init__(self, source=None):
        Reduce.__init__(self, lambda prev, v: prev and bool(v), True, source)


@event_class
class Ema(Op):
    _f1: float
    _f2: float
    _prev: AnyType

    def __init__(self, n=None, weight=None, source=None):
        Op.__init__(self, source)
        self._f1 = weight if weight is not None else 2.0 / (n + 1)
        self._f2 = 1 - self._f1
        self._prev = NO_VALUE

    def on_source(self, *args):
        if self._prev is NO_VALUE:
            value = args
        else:
            value = tuple(self._f2 * p + self._f1 * a for p, a in zip(self._prev, args))
        self._prev = value
        self.emit(*value)


@event_class
class Pairwise(Op):
    _prev: AnyType
    _has_prev: bool

    def __init__(self, source=None):
        Op.__init__(self, source)
        self._has_prev = False

    def on_source(self, *args):
        value = args[0] if len(args) == 1 else args or NO_VALUE
        if self._has_prev:
            self.emit(self._prev, value)
        else:
            self._has_prev = True
        self._prev = value


@event_class
class List(Op):
    _values: list[AnyType]

    def __init__(self, source=None):
        Op.__init__(self)
        self._values = []
        if source is not None:
            self.set_source(source)

    def on_source(self, *args):
        self._values.append(args[0] if len(args) == 1 else args or NO_VALUE)

    def on_source_done(self, source):
        self.emit(self._values)
        Op.on_source_done(self, source)


@event_class
class Deque(Op):
    _count: int
    _q: deque[AnyType]

    def __init__(self, count, source=None):
        Op.__init__(self, source)
        self._count = count
        self._q = deque()

    def on_source(self, *args):
        self._q.append(args[0] if len(args) == 1 else args or NO_VALUE)
        if self._count and len(self._q) > self._count:
            self._q.popleft()
        # A copy: listeners keep what they were given, not a live view.
        self.emit(deque(self._q))
