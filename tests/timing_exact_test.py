"""Exact timing of the timing operators, on a virtual clock."""

import unittest

from eventkit import Event

from .virtual_clock import VirtualClockTestCase


class DelayTest(VirtualClockTestCase, unittest.TestCase):
    def test_values_due_together_keep_their_order(self):
        event = Event.sequence(range(5)).delay(0.5)
        times = []
        event.connect(lambda _: times.append(self.loop.time()))
        self.assertEqual(event.run(), [0, 1, 2, 3, 4])
        self.assertEqual(times, [0.5] * 5)

    def test_completes_after_its_last_value(self):
        e1 = Event.sequence(range(10))
        e2 = Event.sequence(range(100, 110)).delay(0.001)
        self.assertEqual(
            e1.zip(e2).run(), list(zip(range(10), range(100, 110), strict=True))
        )

    def test_one_timer_for_any_number_of_pending_values(self):
        source = Event()
        delayed = source.delay(1.0)
        for i in range(100):
            source.emit(i)
        timers = [
            h for h in self.loop._scheduled if h._callback == delayed._release
        ]
        self.assertEqual(len(timers), 1)


class TimeoutTest(VirtualClockTestCase, unittest.TestCase):
    def test_expires_exactly_at_its_deadline(self):
        event = Event.sequence([1, 2, 3], interval=0.1).timeout(0.01)
        self.assertEqual(event.run(), [1, Event.NO_VALUE])
        self.assertEqual(self.loop.time(), 0.01)

