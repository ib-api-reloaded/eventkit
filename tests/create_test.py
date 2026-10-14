import asyncio
import unittest

from eventkit import Event
from eventkit.util import get_event_loop

from .virtual_clock import VirtualClockTestCase

array1 = list(range(10))
array2 = list(range(100, 110))


class CreateTest(unittest.TestCase):
    def test_wait(self):
        loop = get_event_loop()
        fut = loop.create_future()
        loop.call_later(0.001, fut.set_result, 42)
        event = Event.wait(fut)
        self.assertEqual(event.run(), [42])

    def test_aiterate(self):
        async def ait():
            await asyncio.sleep(0)
            for i in array1:
                yield i

        event = Event.aiterate(ait())
        self.assertEqual(event.run(), array1)

    def test_marble(self):
        s = "   a b c   d e f"
        event = Event.marble(s, interval=0.001)
        self.assertEqual(event.run(), [c for c in "abcdef"])


class SourceTimingTest(VirtualClockTestCase, unittest.TestCase):
    """Sources emit on the event loop's clock, exactly on schedule."""

    def emitTimes(self, event):
        return event.map(lambda _: round(self.loop.time(), 9)).run()

    def test_sequence_interval(self):
        event = Event.sequence([1, 2, 3, 4], interval=0.5)
        self.assertEqual(self.emitTimes(event), [0.0, 0.5, 1.0, 1.5])

    def test_sequence_times_are_relative_to_the_start(self):
        event = Event.sequence("abcd", times=[0, 0.5, 2.0, 2.25])
        self.assertEqual(self.emitTimes(event), [0.0, 0.5, 2.0, 2.25])

    def test_range_interval(self):
        event = Event.range(3, interval=0.25)
        self.assertEqual(self.emitTimes(event), [0.0, 0.25, 0.5])

    def test_timer(self):
        self.assertEqual(Event.timer(0.25, count=3).run(), [0.25, 0.5, 0.75])
        self.assertEqual(self.loop.time(), 0.75)
