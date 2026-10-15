from __future__ import annotations

import functools
from collections import deque
from importlib import import_module
from types import ModuleType
from typing import Any

from ..event import event_class
from ..util import NO_VALUE
from .op import Op


@functools.cache
def _numpy() -> ModuleType:
    """numpy, imported when an Array operator is first created: it is the
    optional ``aeventkit[numpy]`` extra, and ``import eventkit`` must not
    pay for it."""
    try:
        return import_module("numpy")
    except ImportError as error:
        raise ImportError(
            "numpy is required for Array operators. "
            "Install it with: pip install aeventkit[numpy]"
        ) from error


@event_class
class Array(Op):
    _count: int
    _q: deque[Any]

    def __init__(self, count, source=None):
        _numpy()  # fail here, not at the first value, when numpy is missing
        Op.__init__(self, source)
        self._count = count
        self._q = deque()

    def on_source(self, *args):
        self._q.append(args[0] if len(args) == 1 else args or NO_VALUE)
        if self._count and len(self._q) > self._count:
            self._q.popleft()
        self.emit(_numpy().asarray(self._q))

    def min(self) -> ArrayMin:  # type: ignore[override]
        """
        Minimum value.
        """
        return ArrayMin(self)

    def max(self) -> ArrayMax:  # type: ignore[override]
        """
        Maximum value.
        """
        return ArrayMax(self)

    def sum(self) -> ArraySum:  # type: ignore[override]
        """
        Summation.
        """
        return ArraySum(self)

    def prod(self) -> ArrayProd:
        """
        Product.
        """
        return ArrayProd(self)

    def mean(self) -> ArrayMean:  # type: ignore[override]
        """
        Mean value.
        """
        return ArrayMean(self)

    def std(self) -> ArrayStd:  # type: ignore[override]
        """
        Sample standard deviation.
        """
        return ArrayStd(self)

    def any(self) -> ArrayAny:  # type: ignore[override]
        """
        Test if any array value is true.
        """
        return ArrayAny(self)

    def all(self) -> ArrayAll:  # type: ignore[override]
        """
        Test if all array values are true.
        """
        return ArrayAll(self)


@event_class
class ArrayMin(Op):
    def on_source(self, arg):
        self.emit(arg.min())


@event_class
class ArrayMax(Op):
    def on_source(self, arg):
        self.emit(arg.max())


@event_class
class ArraySum(Op):
    def on_source(self, arg):
        self.emit(arg.sum())


@event_class
class ArrayProd(Op):
    def on_source(self, arg):
        self.emit(arg.prod())


@event_class
class ArrayMean(Op):
    def on_source(self, arg):
        self.emit(arg.mean())


@event_class
class ArrayStd(Op):
    def on_source(self, arg):
        self.emit(arg.std(ddof=1) if len(arg) > 1 else _numpy().nan)


@event_class
class ArrayAny(Op):
    def on_source(self, arg):
        self.emit(arg.any())


@event_class
class ArrayAll(Op):
    def on_source(self, arg):
        self.emit(arg.all())
