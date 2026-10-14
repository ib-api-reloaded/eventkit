import asyncio
import subprocess
import sys
import textwrap
import threading
import unittest
import warnings

from eventkit import Event
from eventkit.util import get_event_loop


class GetEventLoopTest(unittest.TestCase):
    def setUp(self):
        asyncio.set_event_loop(None)

    def tearDown(self):
        loop = get_event_loop()
        asyncio.set_event_loop(None)
        loop.close()

    def test_running_loop_wins(self):
        async def inner():
            return get_event_loop() is asyncio.get_running_loop()

        loop = asyncio.new_event_loop()
        try:
            self.assertTrue(loop.run_until_complete(inner()))
        finally:
            loop.close()

    def test_loop_set_for_the_thread_is_used(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.assertIs(get_event_loop(), loop)

    def test_missing_loop_is_created_and_set(self):
        loop = get_event_loop()
        self.assertFalse(loop.is_closed())
        self.assertIs(get_event_loop(), loop)
        self.assertIs(asyncio.get_event_loop(), loop)

    def test_closed_loop_is_replaced_and_set(self):
        closed = asyncio.new_event_loop()
        asyncio.set_event_loop(closed)
        closed.close()
        loop = get_event_loop()
        self.assertIsNot(loop, closed)
        self.assertFalse(loop.is_closed())
        self.assertIs(get_event_loop(), loop)

    def test_each_thread_gets_its_own_loop(self):
        main = get_event_loop()
        seen = {}

        def worker():
            loop = get_event_loop()
            seen["loop"] = loop
            seen["again"] = get_event_loop()
            asyncio.set_event_loop(None)
            loop.close()

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertIsNot(seen["loop"], main)
        self.assertIs(seen["again"], seen["loop"])

    def test_no_deprecation_warning_in_a_worker_thread(self):
        caught = []

        def worker():
            with warnings.catch_warnings(record=True) as seen:
                warnings.simplefilter("always")
                loop = get_event_loop()
                caught.extend(seen)
            asyncio.set_event_loop(None)
            loop.close()

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(
            [w for w in caught if issubclass(w.category, DeprecationWarning)], []
        )

    def test_no_deprecation_warning_in_a_fresh_interpreter(self):
        """The main thread of a new interpreter, with no loop set yet, is
        where Python 3.12 - 3.13 warn and 3.14 refuses to create a loop."""
        snippet = textwrap.dedent(
            """
            import asyncio
            from eventkit.util import get_event_loop

            loop = get_event_loop()
            assert asyncio.get_event_loop() is loop
            assert get_event_loop() is loop
            loop.close()
            """
        )
        result = subprocess.run(
            [sys.executable, "-W", "error::DeprecationWarning", "-c", snippet],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sync_event_run_uses_the_loop_set_for_the_thread(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = Event.sequence([1]).map(lambda _: asyncio.get_running_loop()).run()
        self.assertEqual(result, [loop])
