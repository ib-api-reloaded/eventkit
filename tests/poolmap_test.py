"""Event.poolmap on concurrent.futures executors.

These run on a real event loop: results arrive from executor threads and
processes, not from timers.
"""

import asyncio
import concurrent.futures
import threading
import time
import unittest
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from eventkit import Event

VALUES = st.lists(st.integers(-50, 50), max_size=30)


def square(x):
    return x * x


def fails_on_negative(x):
    if x < 0:
        raise ValueError(x)
    return x


def run(build):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return build().run()
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def drive(build, limit=10.0):
    """Run the operator ``build()`` returns until it is done; return it with
    the values and the errors it emitted (awaiting it would raise the first
    error instead)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    values: list[Any] = []
    errors: list[tuple[Event, Exception]] = []
    try:
        op = build()
        op.connect(
            values.append, error=lambda source, error: errors.append((source, error))
        )

        async def until_done():
            while not op.done():
                await asyncio.sleep(0.005)

        loop.run_until_complete(asyncio.wait_for(until_done(), limit))
        return op, values, errors
    finally:
        asyncio.set_event_loop(None)
        loop.close()


@settings(max_examples=40, deadline=None)
@given(VALUES, st.integers(1, 6))
def test_results_are_those_of_map_in_order(values, chunksize):
    with ThreadPoolExecutor(4) as pool:
        result = run(
            lambda: Event.sequence(values).poolmap(pool, square, chunksize=chunksize)
        )
    assert result == [square(v) for v in values]


@settings(max_examples=40, deadline=None)
@given(VALUES, st.integers(1, 6))
def test_unordered_results_are_the_same_values(values, chunksize):
    with ThreadPoolExecutor(4) as pool:
        result = run(
            lambda: Event.sequence(values).poolmap(
                pool, square, chunksize=chunksize, ordered=False
            )
        )
    assert Counter(result) == Counter(square(v) for v in values)


@settings(max_examples=40, deadline=None)
@given(VALUES, st.integers(1, 6))
def test_a_failing_value_does_not_fail_its_chunk(values, chunksize):
    with ThreadPoolExecutor(2) as pool:
        op, result, errors = drive(
            lambda: Event.sequence(values).poolmap(
                pool, fails_on_negative, chunksize=chunksize
            )
        )
    assert result == [v for v in values if v >= 0]
    assert [e.args[0] for _, e in errors] == [v for v in values if v < 0]
    assert all(source is op for source, _ in errors)


class PoolMapTest(unittest.TestCase):
    def test_process_pool(self):
        with ProcessPoolExecutor(2) as pool:
            result = run(lambda: Event.range(20).poolmap(pool, abs, chunksize=3))
        self.assertEqual(result, list(range(20)))

    @unittest.skipUnless(
        hasattr(concurrent.futures, "InterpreterPoolExecutor"), "Python 3.14+"
    )
    def test_interpreter_pool(self):
        # Looked up by name: the executor exists only on Python 3.14+.
        executor = getattr(concurrent.futures, "InterpreterPoolExecutor")
        with executor(2) as pool:
            result = run(lambda: Event.range(10).poolmap(pool, abs, chunksize=4))
        self.assertEqual(result, list(range(10)))

    def test_the_last_partial_chunk_is_sent(self):
        with ThreadPoolExecutor(2) as pool:
            result = run(lambda: Event.range(5).poolmap(pool, square, chunksize=2))
        self.assertEqual(result, [0, 1, 4, 9, 16])

    def test_timeout_errors_and_cancels_a_chunk_not_started(self):
        started = []
        gate = threading.Event()

        def slow(x):
            started.append(x)
            gate.wait(5)
            return x

        with ThreadPoolExecutor(1) as pool:
            try:
                _, result, errors = drive(
                    lambda: Event.sequence([1, 2]).poolmap(pool, slow, timeout=0.1)
                )
            finally:
                gate.set()  # let the one running call finish
        self.assertEqual(result, [])
        self.assertEqual(len(errors), 2)
        self.assertTrue(all(isinstance(e, TimeoutError) for _, e in errors))
        self.assertEqual(started, [1])  # 2 was cancelled before it ran

    def test_run_cleanup_cancels_work_not_started(self):
        started = []

        def record(x):
            started.append(x)
            time.sleep(0.05)
            return x

        with ThreadPoolExecutor(1) as pool:
            first = run(lambda: Event.range(50).poolmap(pool, record).take(1))
            self.assertEqual(first, [0])
            # poolmap never shuts the executor down
            self.assertEqual(pool.submit(square, 3).result(), 9)
        self.assertLess(len(started), 50)

    def test_chunksize_must_be_positive(self):
        with ThreadPoolExecutor(1) as pool, self.assertRaises(ValueError):
            Event("e").poolmap(pool, square, chunksize=0)
