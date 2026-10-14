"""Errors reach error listeners as ``(source, error)``, and are logged when
nobody listens."""

import asyncio
import logging
import unittest

from eventkit import Event

from .virtual_clock import VirtualClockTestCase


class ErrorEvent(Exception):
    pass


def failing(*args):
    raise ErrorEvent("listener failed")


class ListenerErrorTest(unittest.TestCase):
    def test_listener_error_goes_to_error_listeners(self):
        event = Event("e")
        seen = []
        event.connect(failing, error=lambda source, error: seen.append((source, error)))
        event.emit(1)
        [(source, error)] = seen
        self.assertIs(source, event)
        self.assertIsInstance(error, ErrorEvent)

    def test_listener_error_is_logged_when_nobody_listens_for_errors(self):
        event = Event("e")
        event.connect(failing)
        with self.assertLogs("eventkit.event", logging.ERROR) as logs:
            event.emit(1)
        self.assertIn("listener failed", "\n".join(logs.output))


class OperatorErrorTest(VirtualClockTestCase, unittest.TestCase):
    def errors_of(self, op):
        seen = []
        op.error_event.connect(lambda source, error: seen.append((source, error)))
        return seen

    def test_delay_forwards_errors_after_the_delay(self):
        src = Event("src")
        delayed = src.delay(0.5)
        seen = self.errors_of(delayed)
        error = ErrorEvent("upstream")
        src.error_event.emit(src, error)
        self.assertEqual(seen, [])
        self.loop.run_until_complete(asyncio.sleep(0.5))
        self.assertEqual(seen, [(src, error)])

    def test_end_on_error_forwards_the_error_and_ends(self):
        src = Event("src")
        op = src.end_on_error()
        seen = self.errors_of(op)
        values = []
        op.connect(values.append)
        src.emit(1)
        error = ErrorEvent("upstream")
        src.error_event.emit(src, error)
        src.emit(2)
        self.assertEqual(values, [1])
        self.assertEqual(seen, [(src, error)])
        self.assertTrue(op.done())

    def test_async_map_errors_reach_awaiters(self):
        async def boom(x):
            raise ErrorEvent(f"bad {x}")

        with self.assertRaises(ErrorEvent):
            Event.sequence([1, 2]).map(boom).run()

    def test_async_map_error_listener_gets_the_map_as_source(self):
        async def boom(x):
            raise ErrorEvent(f"bad {x}")

        mapped = Event.sequence([1]).map(boom)
        seen = self.errors_of(mapped)
        self.loop.run_until_complete(asyncio.sleep(0.1))
        [(source, error)] = seen
        self.assertIs(source, mapped)
        self.assertIsInstance(error, ErrorEvent)
