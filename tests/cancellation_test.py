"""Cancellation Tests"""

import asyncio
import logging

import pytest

import eventkit as ev

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_aiterate_cancellation():
    """Verify that breaking an async for loop cancels the source Aiterate task."""
    generator_finished_cleanly = False

    async def my_generator():
        nonlocal generator_finished_cleanly
        try:
            for i in range(10):
                yield i
                await asyncio.sleep(0.1)
        finally:
            generator_finished_cleanly = True

    event = ev.Event.aiterate(my_generator())
    count = 0
    async for _ in event:
        count += 1
        if count == 2:
            break
    await asyncio.sleep(0.1)

    assert generator_finished_cleanly, "Generator was not cancelled correctly."


@pytest.mark.asyncio
async def test_wait_cancellation():
    """Verify that cancelling a Wait event cancels the underlying Future."""
    long_running_future = asyncio.Future()
    wait_event = ev.Event.wait(long_running_future)
    assert not long_running_future.done()
    wait_event.cancel()
    await asyncio.sleep(0)
    assert long_running_future.cancelled()


@pytest.mark.asyncio
async def test_timer_cancellation():
    """Verify that breaking an async for loop cancels the source Timer task."""
    # Use ev.Timer directly
    interval = 0.1
    count_limit = 10  # The timer will try to emit 10 times
    num_to_take = 2  # We will take 2 items, triggering cancellation

    timer_event = ev.Timer(interval, count=count_limit)

    results = []
    try:
        async for item in timer_event.take(num_to_take):
            results.append(item)
            await asyncio.sleep(0)  # Yield control
    except asyncio.CancelledError:
        pass

    assert len(results) == num_to_take

    # Give some time for cancellation to propagate
    await asyncio.sleep(interval * (count_limit - num_to_take) + 0.1)

    # The timer should be done after cancellation
    assert timer_event.done()


@pytest.mark.asyncio
async def test_map_cancellation():
    """Verify that Map cancels its in-flight tasks."""
    processed_items = []
    tasks_cancelled = []

    async def long_running_map(x):
        try:
            processed_items.append(x)
            # Make the first two items fast and the rest slow
            sleep_time = 0.1 if x < 2 else 1
            await asyncio.sleep(sleep_time)
            return x
        except asyncio.CancelledError:
            tasks_cancelled.append(x)
            raise

    event = ev.Range(10).map(long_running_map, task_limit=5).take(2)
    results = []
    try:
        async for item in event:
            results.append(item)
    except asyncio.CancelledError:
        pass

    assert len(results) == 2
    # Wait long enough for the slow tasks to be cancelled
    await asyncio.sleep(1.5)

    assert len(processed_items) < 6
    assert len(processed_items) > 1
    assert len(tasks_cancelled) == 3


@pytest.mark.asyncio
async def test_map_cancellation_unordered():
    """Verify that Map(ordered=False) cancels its in-flight tasks."""
    processed_items = []
    tasks_cancelled = []

    async def long_running_map(x):
        try:
            processed_items.append(x)
            # Make the first two items fast and the rest slow
            sleep_time = 0.1 if x < 2 else 1.0
            await asyncio.sleep(sleep_time)
            return x
        except asyncio.CancelledError:
            tasks_cancelled.append(x)
            raise

    event = ev.Range(10).map(long_running_map, task_limit=5, ordered=False).take(2)
    results = []
    try:
        async for item in event:
            results.append(item)
            await asyncio.sleep(0)  # Yield control to event loop
    except asyncio.CancelledError:
        pass

    assert len(results) == 2
    await asyncio.sleep(1.5)

    assert len(processed_items) <= 6
    assert len(processed_items) > 1
    assert len(tasks_cancelled) == 3


@pytest.mark.asyncio
async def test_map_cancellation_general_case():
    """Verify general cancellation behavior for unordered Map with multiple tasks."""
    processed_items = []
    tasks_cancelled = []
    num_to_take = 3
    total_items = 10
    task_limit = 5

    async def long_running_map(x):
        try:
            processed_items.append(x)
            # Make the first num_to_take items fast and the rest slow
            sleep_time = 0.1 if x < num_to_take else 1.0
            await asyncio.sleep(sleep_time)
            return x
        except asyncio.CancelledError:
            tasks_cancelled.append(x)
            raise

    event = (
        ev.Range(total_items)
        .map(long_running_map, task_limit=task_limit, ordered=False)
        .take(num_to_take)
    )
    results = []
    try:
        async for item in event:
            results.append(item)
            await asyncio.sleep(0)  # Yield control to event loop
    except asyncio.CancelledError:
        pass

    assert len(results) == num_to_take

    # Wait for cancellation to propagate
    await asyncio.sleep(1.5)

    # Expected behavior:
    # - num_to_take items are received.
    # - task_limit tasks are started initially.
    # - Some tasks might complete before cancellation.
    # - The remaining in-flight tasks should be cancelled.
    # - The number of processed_items should be num_to_take + (tasks that started
    # before cancellation but were not cancelled)
    # - The number of tasks_cancelled should be the remaining in-flight tasks.

    # A more robust assertion for processed_items:
    # It should be at least num_to_take (the ones we received)
    # And at most task_limit + num_to_take (the ones that could have started before
    # cancellation)
    assert len(processed_items) >= num_to_take
    assert (
        len(processed_items) <= task_limit + num_to_take
    )  # Max possible started tasks

    # We expect a significant number of tasks to be cancelled.
    # The number of tasks that were started but not completed and then cancelled.
    # This should be roughly total_items - num_to_take - (tasks that completed before
    # cancellation)
    assert len(tasks_cancelled) > 0
    assert (
        len(tasks_cancelled) <= total_items - num_to_take
    )  # Max possible cancelled tasks


@pytest.mark.asyncio
async def test_chained_map_cancellation():
    """Verify cancellation propagates through chained Map operators."""
    processed_items_1 = []
    tasks_cancelled_1 = []
    processed_items_2 = []
    tasks_cancelled_2 = []

    num_to_take = 2
    total_items = 10
    task_limit_1 = 5
    task_limit_2 = 3

    async def long_running_map_1(x):
        try:
            processed_items_1.append(x)
            # Make the first num_to_take items fast, the rest slow
            sleep_time = 0.1 if x < num_to_take else 1.0
            await asyncio.sleep(sleep_time)
            return x
        except asyncio.CancelledError:
            tasks_cancelled_1.append(x)
            raise

    async def long_running_map_2(x):
        try:
            processed_items_2.append(x)
            # Make all tasks in Map2 slow
            sleep_time = 1.0
            await asyncio.sleep(sleep_time)
            return x
        except asyncio.CancelledError:
            tasks_cancelled_2.append(x)
            raise

    event = (
        ev.Range(total_items)
        .map(long_running_map_1, task_limit=task_limit_1, ordered=False)
        .map(long_running_map_2, task_limit=task_limit_2, ordered=False)
        .take(num_to_take)
    )
    results = []
    try:
        async for item in event:
            results.append(item)
            await asyncio.sleep(0)  # Yield control to event loop
    except asyncio.CancelledError:
        pass

    assert len(results) == num_to_take

    # Wait for cancellation to propagate through both layers
    await asyncio.sleep(1.5)

    # Assertions for Map1
    assert len(processed_items_1) >= num_to_take
    assert len(processed_items_1) <= total_items
    assert len(tasks_cancelled_1) > 0
    assert len(tasks_cancelled_1) <= total_items - num_to_take

    # Assertions for Map2
    assert len(processed_items_2) >= num_to_take
    assert len(processed_items_2) <= total_items
    assert len(tasks_cancelled_2) > 0
    assert len(tasks_cancelled_2) <= total_items - num_to_take


@pytest.mark.asyncio
async def test_aiterate_source_map_cancellation():
    """Verify cancellation propagates from Map back to an Aiterate source."""
    generator_finished_cleanly = False
    processed_items = []
    tasks_cancelled = []

    num_to_take = 3
    total_items = 10
    task_limit = 5

    async def my_generator():
        nonlocal generator_finished_cleanly
        try:
            for i in range(total_items):
                yield i
                await asyncio.sleep(0.1)  # Simulate some work
        finally:
            generator_finished_cleanly = True

    async def long_running_map(x):
        try:
            processed_items.append(x)
            # Make the first num_to_take items fast, the rest slow
            sleep_time = 0.1 if x < num_to_take else 1.0
            await asyncio.sleep(sleep_time)
            return x
        except asyncio.CancelledError:
            tasks_cancelled.append(x)
            raise

    event = (
        ev.Event.aiterate(my_generator())
        .map(long_running_map, task_limit=task_limit, ordered=False)
        .take(num_to_take)
    )
    results = []
    try:
        async for item in event:
            results.append(item)
            await asyncio.sleep(0)  # Yield control to event loop
    except asyncio.CancelledError:
        pass

    assert len(results) == num_to_take

    # Wait for cancellation to propagate
    await asyncio.sleep(1.5)

    # Assertions for Map
    assert len(processed_items) >= num_to_take
    assert len(processed_items) <= total_items
    assert len(tasks_cancelled) > 0
    assert len(tasks_cancelled) <= total_items - num_to_take

    # Assertions for Aiterate source
    assert generator_finished_cleanly, "Aiterate generator was not cancelled cleanly."
