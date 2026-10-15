"""Operators stop when they are done, keep running while they are used, and
``Event.run()`` cleans up exactly what its own pipeline started."""

import asyncio
import gc
import logging
import threading
import unittest
import warnings

import eventkit
from eventkit import Event, util
from eventkit.ops.timing import Timeout
from eventkit.ops.transform import Map, Mergemap
from eventkit.util import get_event_loop

from .virtual_clock import VirtualClockTestCase


class ThreadsafeEmitTest(unittest.TestCase):
    def test_emit_from_a_worker_thread_runs_on_the_main_loop(self):
        loop = get_event_loop()
        event = Event("e")
        seen = []
        event.connect(lambda x: seen.append((x, threading.current_thread())))
        worker = threading.Thread(target=lambda: event.emit_threadsafe(1))
        worker.start()
        worker.join()
        loop.run_until_complete(asyncio.sleep(0))
        self.assertEqual(seen, [(1, threading.main_thread())])


class ThrottleTest(VirtualClockTestCase, unittest.TestCase):
    def test_a_value_costing_the_whole_budget_passes_alone(self):
        times = []
        throttled = Event.sequence([1, 2, 3]).throttle(1, 0.5)
        throttled.connect(lambda _: times.append(self.loop.time()))
        self.assertEqual(throttled.run(), [1, 2, 3])
        self.assertEqual(len(times), 3)
        self.assertGreaterEqual(times[1] - times[0], 0.5)
        self.assertGreaterEqual(times[2] - times[1], 0.5)

    def test_an_oversized_cost_does_not_stall(self):
        throttled = Event.sequence([5, 50, 5]).throttle(10, 0.5, cost_func=lambda x: x)
        self.assertEqual(throttled.run(), [5, 50, 5])


class TimeoutTest(VirtualClockTestCase, unittest.TestCase):
    def test_nothing_is_forwarded_after_the_timeout(self):
        src = Event("src")
        op = src.timeout(0.1)
        seen = []
        op.connect(lambda *args: seen.append(args))
        self.loop.run_until_complete(asyncio.sleep(0.2))
        self.assertTrue(op.done())
        src.emit(1)
        self.assertEqual(seen, [()])  # the timeout itself, nothing after

    def test_attached_to_a_finished_source_never_fires(self):
        op = Timeout(0.1)  # not attached to a source yet
        done = Event("done")
        done.set_done()
        fired = []
        op.connect(lambda *args: fired.append(args))
        op.set_source(done)
        self.loop.run_until_complete(asyncio.sleep(0.2))
        self.assertEqual(fired, [])


class SkipTest(unittest.TestCase):
    def test_skip_keeps_working_without_another_reference(self):
        src = Event("src")
        seen = []
        src.skip(1).connect(seen.append)
        src.emit(1)
        gc.collect()
        src.emit(2)
        gc.collect()
        src.emit(3)
        self.assertEqual(seen, [2, 3])


class DeadWeakrefTest(unittest.TestCase):
    def test_a_collected_listener_is_skipped_not_called_without_self(self):
        class Listener:
            def __init__(self):
                self.calls = 0

            def method(self, x):
                self.calls += 1

        event = Event("e")
        listener = Listener()
        event.connect(listener.method)
        # The weak reference dies while the slot is still listed (as during
        # an emit, or on PyPy before the weakref callback has run).
        slot = event._slots.slots[0]
        slot.weakref = lambda: None
        errors = []
        event.error_event.connect(lambda source, error: errors.append(error))
        event.emit(1)
        self.assertEqual(errors, [])
        self.assertEqual(listener.calls, 0)


class RunCleanupTest(VirtualClockTestCase, unittest.TestCase):
    def test_run_does_not_cancel_a_future_it_only_waits_on(self):
        future = self.loop.create_future()
        self.loop.call_later(1.0, future.set_result, "late")
        Event.wait(future).timeout(0.1).run()
        self.assertFalse(future.cancelled())
        self.assertEqual(self.loop.run_until_complete(future), "late")

    def test_run_leaves_a_source_shared_with_another_pipeline_running(self):
        shared = Event.timer(0.1, count=10)
        other = []
        shared.connect(other.append)
        self.assertEqual(len(shared.take(2).run()), 2)
        self.loop.run_until_complete(asyncio.sleep(0.25))
        self.assertGreater(len(other), 2)

    def test_run_stops_a_side_input_it_started(self):
        timer = Event.timer(1.0)  # infinite
        result = Event.range(10, interval=0.4).sample(timer).take(2).run()
        self.assertEqual(len(result), 2)
        self.assertIsNone(timer._task)

    def test_a_cancelled_async_iterator_source_ends(self):
        source = Event.timer(1.0)
        ended = []
        source.done_event.connect(lambda _: ended.append(True))
        source._task.cancel()
        self.loop.run_until_complete(asyncio.sleep(0))
        self.assertEqual(ended, [True])
        self.assertTrue(source.done())

    def test_wait_on_a_cancelled_future_ends_quietly(self):
        future = self.loop.create_future()
        waited = Event.wait(future)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            future.cancel()
            self.loop.run_until_complete(asyncio.sleep(0))
        self.assertTrue(waited.done())


class SkipZeroTest(unittest.TestCase):
    def test_skip_zero_passes_everything(self):
        src = Event("src")
        seen = []
        op = src.skip(0)
        op.connect(seen.append)
        src.emit(1)
        src.emit(2)
        self.assertEqual(seen, [1, 2])


class MainThreadLoopTest(unittest.TestCase):
    def test_a_worker_never_gets_a_loop_nobody_runs(self):
        saved = util._main_thread.loop
        util._main_thread.loop = None
        errors = []

        def worker():
            try:
                Event("e").emit_threadsafe(1)
            except RuntimeError as error:
                errors.append(error)

        try:
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()
        finally:
            util._main_thread.loop = saved
        [error] = errors
        self.assertIn("main thread has no event loop", str(error))


class DisconnectTest(unittest.TestCase):
    def test_disconnect_undoes_one_connect(self):
        event, seen = Event("e"), []
        event.connect(seen.append)
        event.connect(seen.append)
        event.disconnect(seen.append)
        event.emit(1)
        self.assertEqual(seen, [1])
        event.disconnect(seen.append)
        event.disconnect(seen.append)  # not connected: nothing to do
        event.emit(2)
        self.assertEqual(seen, [1])

    def test_disconnect_all_removes_every_connection_and_callbacks(self):
        event, seen, done = Event("e"), [], []
        for _ in range(3):
            event.connect(seen.append, done=done.append)
        event.disconnect_all(seen.append, done=done.append)
        event.emit(1)
        event.set_done()
        self.assertEqual((seen, done), ([], []))

    def test_disconnect_detaches_an_operator_connected_with_connect(self):
        source, mapped = Event("source"), Map(abs)  # an operator without source
        seen = []
        mapped.connect(seen.append)
        source.connect(mapped)
        source.emit(-1)
        source.disconnect(mapped)
        source.emit(-2)
        self.assertEqual(seen, [1])
        self.assertIsNone(mapped._source)


class OperatorEdgeTest(VirtualClockTestCase, unittest.TestCase):
    def test_take_zero_is_done_at_once(self):
        source = Event("source")
        op = source.take(0)
        self.assertTrue(op.done())
        self.assertEqual(len(source), 0)  # never attached

    def test_an_emap_attached_after_construction(self):
        op = Mergemap(lambda x: Event.sequence([x, x * 10]))
        Event.sequence([1, 2]).connect(op)
        self.assertEqual(sorted(op.run()), [1, 2, 10, 20])

    def test_a_cancelled_async_map_task_is_neither_value_nor_error(self):
        async def slow(x):
            await asyncio.sleep(10)
            return x

        source = Event("source")
        mapped = source.map(slow)
        seen, errors = [], []
        mapped.connect(seen.append, error=lambda s, e: errors.append(e))
        source.emit(1)
        mapped._tasks[0].cancel()
        with self.assertNoLogs(level=logging.ERROR):
            self.loop.run_until_complete(asyncio.sleep(0.1))
        self.assertEqual((seen, errors), ([], []))

    def test_a_failing_async_map_emits_the_error_and_no_value(self):
        async def boom(x):
            raise ValueError(x)

        source = Event("source")
        mapped = source.map(boom)
        seen, errors = [], []
        mapped.connect(seen.append, error=lambda s, e: errors.append(e))
        source.emit(1)
        self.loop.run_until_complete(asyncio.sleep(0.1))
        self.assertEqual(seen, [])
        self.assertEqual([str(e) for e in errors], ["1"])

    def test_an_unhandled_operator_error_is_logged_with_its_traceback(self):
        source = Event("source")
        source.map(abs)  # no error listener anywhere downstream
        try:
            raise ValueError("upstream")
        except ValueError as error:
            failure = error
        with self.assertLogs("eventkit", logging.ERROR) as logs:
            source.error_event.emit(source, failure)
        self.assertIs(logs.records[0].exc_info[1], failure)


def test_array_prod_is_exported():
    assert eventkit.ArrayProd.__name__ == "ArrayProd"
