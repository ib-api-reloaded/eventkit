"""Tests for how an event holds its listeners under garbage collection.

An event holds a function, a built-in or a ``functools.partial``
strongly. It holds a bound method through a weak reference to the
method's instance. It holds a callable object through a weak reference
to the object if the object supports weak references, and strongly
otherwise. These tests check the rule on the slot records directly, so
the check is the same on every interpreter. They also check that a
weakly held listener is disconnected when its object is collected, and
that a strongly held listener keeps working after collections.

The join operators own the callbacks they connect to their sources, so
those callbacks are held strongly. A pipeline that is referenced only
through its sources and its final listener must keep delivering values
across collections.
"""

import functools
import gc
import unittest

from eventkit import Event
from eventkit.util import NO_VALUE


class Counter:
    def __init__(self):
        self.calls = 0

    def method(self, *args):
        self.calls += 1

    def __call__(self, *args):
        self.calls += 1


class SlottedCounter:
    """A callable object that has no ``__weakref__`` attribute."""

    __slots__ = ("calls",)

    def __init__(self):
        self.calls = 0

    def __call__(self, *args):
        self.calls += 1


def slotOf(event: Event):
    [slot] = event._slots.slots
    return slot


class ReferenceRuleTest(unittest.TestCase):
    def test_function_is_held_strongly(self):
        event = Event()
        event.connect(lambda *a: None)
        self.assertIsNone(slotOf(event).weakref)

    def test_partial_is_held_strongly(self):
        event = Event()
        received: list[int] = []
        event.connect(functools.partial(received.append))
        self.assertIsNone(slotOf(event).weakref)
        gc.collect()
        event.emit(1)
        self.assertEqual(received, [1])

    def test_builtin_method_is_held_strongly(self):
        event = Event()
        received: list[int] = []
        event.connect(received.append)
        self.assertIsNone(slotOf(event).weakref)

    def test_bound_method_is_held_weakly(self):
        event = Event()
        counter = Counter()
        event.connect(counter.method)
        self.assertIsNotNone(slotOf(event).weakref)
        event.emit()
        self.assertEqual(counter.calls, 1)

    def test_bound_method_with_keep_ref_is_held_strongly(self):
        event = Event()
        event.connect(Counter().method, keep_ref=True)
        self.assertIsNone(slotOf(event).weakref)

    def test_callable_object_is_held_weakly(self):
        """The test keeps a reference to the instance. The slot still holds
        the instance weakly."""
        event = Event()
        counter = Counter()
        event.connect(counter)
        self.assertIsNotNone(slotOf(event).weakref)

    def test_callable_object_without_weakref_is_held_strongly(self):
        event = Event()
        counter = SlottedCounter()
        event.connect(counter)
        self.assertIsNone(slotOf(event).weakref)
        del counter
        gc.collect()
        event.emit()
        self.assertEqual(event._slots.count, 1)

    def test_collected_instance_disconnects_its_method(self):
        event = Event()
        counter = Counter()
        event.connect(counter.method)
        del counter
        gc.collect()
        event.emit()
        self.assertEqual(event._slots.count, 0)


class JoinOperatorReferenceTest(unittest.TestCase):
    """In these tests, only the two sources and the listener reference the
    operator."""

    def test_merge_delivers_across_collections(self):
        source, other = Event(), Event()
        received: list[int] = []
        source.merge(other).connect(received.append)
        gc.collect()
        source.emit(1)
        gc.collect()
        other.emit(2)
        self.assertEqual(received, [1, 2])

    def test_ziplatest_delivers_across_collections(self):
        source, other = Event(), Event()
        received: list[tuple] = []
        source.ziplatest(other).connect(lambda *args: received.append(args))
        gc.collect()
        source.emit(1)
        gc.collect()
        other.emit(2)
        self.assertEqual(received, [(1, NO_VALUE), (1, 2)])

    def test_concat_delivers_across_collections(self):
        source, other = Event(), Event()
        received: list[int] = []
        source.concat(other).connect(received.append)
        gc.collect()
        source.emit(1)
        gc.collect()
        source.emit(2)
        self.assertEqual(received, [1, 2])

    def test_chain_delivers_across_collections(self):
        source, other = Event(), Event()
        received: list[int] = []
        source.chain(other).connect(received.append)
        gc.collect()
        source.emit(1)
        gc.collect()
        source.set_done()
        other.emit(2)
        self.assertEqual(received, [1, 2])
