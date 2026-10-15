"""The examples from the GitHub issues, as regression tests.

https://github.com/ib-api-reloaded/eventkit/issues/<number>
"""

import asyncio
import logging
import subprocess
import sys
import unittest
import warnings
from unittest.mock import patch

from eventkit import Event
from eventkit.ops import array

from .virtual_clock import VirtualClockTestCase


class Issue5ListenerExceptionWithoutErrorListener(unittest.TestCase):
    def test_the_exception_is_logged(self):
        event = Event("e")

        def failing(*args):
            raise ValueError("listener failed")

        event += failing
        with self.assertLogs("eventkit.event", logging.ERROR) as logs:
            event.emit(1)
        self.assertIn("listener failed", "\n".join(logs.output))


class Issue6AwaitingDoneEvent(VirtualClockTestCase, unittest.TestCase):
    def test_awaiting_the_done_event_of_an_event(self):
        event = Event("e")
        seen = []

        async def set_done():
            event.emit()
            await asyncio.sleep(1)
            event.set_done()

        async def await_done():
            seen.append(await event.done_event)

        async def main():
            await asyncio.gather(set_done(), await_done())

        self.loop.run_until_complete(main())
        self.assertEqual(seen, [event])


class Issue7NumpyIsOptional(unittest.TestCase):
    def test_importing_eventkit_does_not_import_numpy(self):
        snippet = "import sys, eventkit; assert 'numpy' not in sys.modules"
        subprocess.run([sys.executable, "-c", snippet], check=True)

    def test_array_without_numpy_names_the_extra(self):
        array._numpy.cache_clear()
        try:
            with (
                patch.dict(sys.modules, {"numpy": None}),  # as if not installed
                self.assertRaisesRegex(ImportError, r"aeventkit\[numpy\]"),
            ):
                Event("e").array(3)
        finally:
            array._numpy.cache_clear()


class Issue8EventLoop(unittest.TestCase):
    def test_no_deprecation_warning_outside_a_running_loop(self):
        snippet = (
            "import warnings; warnings.simplefilter('error');"
            "import eventkit as ev; print(ev.Event.sequence([1, 2]).run())"
        )
        out = subprocess.run(
            [sys.executable, "-c", snippet], check=True, capture_output=True, text=True
        )
        self.assertEqual(out.stdout.strip(), "[1, 2]")

    def test_run_inside_a_running_loop_explains_what_to_do(self):
        async def main():
            Event.sequence([1]).run()

        loop = asyncio.new_event_loop()
        try:
            with self.assertRaisesRegex(RuntimeError, r"await event\.list\(\)"):
                loop.run_until_complete(main())
        finally:
            loop.close()
            asyncio.set_event_loop(None)


class Issue10MapWithFutures(VirtualClockTestCase, unittest.TestCase):
    def test_map_to_futures_and_tasks(self):
        def future_of(x):
            future = self.loop.create_future()
            self.loop.call_later(0.1, future.set_result, x * 10)
            return future

        async def double(x):
            return x * 2

        def task_of(x):
            return self.loop.create_task(double(x))

        self.assertEqual(Event.sequence([1, 2, 3]).map(future_of).run(), [10, 20, 30])
        self.assertEqual(Event.sequence([1, 2, 3]).map(task_of).run(), [2, 4, 6])


class Issue12TimeoutForwardsItsSource(VirtualClockTestCase, unittest.TestCase):
    def test_values_faster_than_the_timeout_pass_through(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            result = Event.sequence([1, 2, 3, 4], interval=0.2).timeout(1).run()
        self.assertEqual(result, [1, 2, 3, 4])
