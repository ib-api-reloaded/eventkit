"""Property-based tests: operators against plain-Python reference models,
contracts every operator must keep, and random listener operations.

Every example runs on a fresh virtual-clock loop, so timing is exact and
no test sleeps.
"""

import asyncio
import contextlib
import itertools
import logging
import operator
from collections import deque

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from eventkit import Event
from eventkit.util import NO_VALUE

from .virtual_clock import VirtualClockLoop

VALUES = st.lists(st.integers(-20, 20), max_size=20)
COUNT = st.integers(0, 6)
POSITIVE = st.integers(1, 6)

settings.register_profile("eventkit", max_examples=150, deadline=None)
settings.load_profile("eventkit")


@contextlib.contextmanager
def virtual_loop():
    loop = VirtualClockLoop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        asyncio.set_event_loop(None)
        loop.close()


class _Errors(logging.Handler):
    """Collects ERROR records, so a swallowed exception fails the test."""

    def __init__(self):
        super().__init__(logging.ERROR)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@contextlib.contextmanager
def no_logged_errors():
    handler = _Errors()
    loggers = [logging.getLogger("eventkit"), logging.getLogger("asyncio")]
    for log in loggers:
        log.addHandler(handler)
    try:
        yield
    finally:
        for log in loggers:
            log.removeHandler(handler)
    assert not handler.records, [r.getMessage() for r in handler.records]


def collect(build):
    """``build()`` a pipeline on a fresh loop; return ``run()``'s list,
    checking that nothing is emitted after the pipeline is done."""
    with virtual_loop(), no_logged_errors():
        event = build()
        late = []
        event.connect(lambda *args: late.append(args) if event.done() else None)
        result = event.run()
        assert late == [], f"emitted after done: {late}"
        return result


def accumulated(values, func, initial=NO_VALUE):
    if initial is NO_VALUE:
        return list(itertools.accumulate(values, func))[1:]
    return list(itertools.accumulate(values, func, initial=initial))[1:]


# Operators against reference models


@given(VALUES)
def test_map(values):
    assert collect(lambda: Event.sequence(values).map(lambda x: 3 * x + 1)) == [
        3 * x + 1 for x in values
    ]


@given(VALUES)
def test_filter(values):
    assert collect(lambda: Event.sequence(values).filter(lambda x: x % 3 == 0)) == [
        x for x in values if x % 3 == 0
    ]


@given(VALUES, COUNT)
def test_skip(values, n):
    assert collect(lambda: Event.sequence(values).skip(n)) == values[n:]


@given(VALUES, COUNT)
def test_take(values, n):
    assert collect(lambda: Event.sequence(values).take(n)) == values[:n]


@given(VALUES)
def test_takewhile(values):
    assert collect(lambda: Event.sequence(values).takewhile(lambda x: x >= 0)) == list(
        itertools.takewhile(lambda x: x >= 0, values)
    )


@given(VALUES)
def test_dropwhile(values):
    assert collect(lambda: Event.sequence(values).dropwhile(lambda x: x >= 0)) == list(
        itertools.dropwhile(lambda x: x >= 0, values)
    )


@given(VALUES, st.integers(-5, 5), st.integers(-3, 3))
def test_enumerate(values, start, step):
    assert collect(lambda: Event.sequence(values).enumerate(start, step)) == [
        (start + i * step, x) for i, x in enumerate(values)
    ]


@given(VALUES, st.integers(-5, 5), st.integers(-3, 3))
def test_count(values, start, step):
    assert collect(lambda: Event.sequence(values).count(start, step)) == [
        start + i * step for i in range(len(values))
    ]


@given(VALUES, st.integers(-9, 9))
def test_constant(values, constant):
    assert collect(lambda: Event.sequence(values).constant(constant)) == [
        constant
    ] * len(values)


@given(VALUES, st.lists(st.integers(), max_size=25))
def test_iterate(values, items):
    assert collect(lambda: Event.sequence(values).iterate(iter(items))) == [
        item for _, item in zip(values, items)
    ]


@given(VALUES)
def test_reduce_without_initializer(values):
    assert collect(lambda: Event.sequence(values).reduce(operator.sub)) == (
        accumulated(values, operator.sub)
    )


@given(VALUES, st.integers(-9, 9))
def test_reduce_with_initializer(values, initial):
    assert collect(lambda: Event.sequence(values).reduce(operator.sub, initial)) == (
        accumulated(values, operator.sub, initial)
    )


@given(VALUES, st.integers(-9, 9))
def test_sum(values, start):
    assert collect(lambda: Event.sequence(values).sum(start)) == accumulated(
        values, operator.add, start
    )


@given(VALUES)
def test_min_max(values):
    assert collect(lambda: Event.sequence(values).min()) == accumulated(
        values, min, float("inf")
    )
    assert collect(lambda: Event.sequence(values).max()) == accumulated(
        values, max, -float("inf")
    )


@given(VALUES)
def test_any_all(values):
    assert collect(lambda: Event.sequence(values).any()) == accumulated(
        values, lambda acc, v: acc or bool(v), False
    )
    assert collect(lambda: Event.sequence(values).all()) == accumulated(
        values, lambda acc, v: acc and bool(v), True
    )


@given(VALUES)
def test_mean(values):
    result = collect(lambda: Event.sequence(values).mean())
    expected = [sum(values[: i + 1]) / (i + 1) for i in range(len(values))]
    assert len(result) == len(expected)
    for got, want in zip(result, expected):
        assert abs(got - want) < 1e-9


@given(VALUES)
def test_pairwise(values):
    assert collect(lambda: Event.sequence(values).pairwise()) == list(
        itertools.pairwise(values)
    )


@given(VALUES, POSITIVE)
def test_previous(values, count):
    assert collect(lambda: Event.sequence(values).previous(count)) == values[:-count]


@given(VALUES)
def test_changes(values):
    assert collect(lambda: Event.sequence(values).changes()) == [
        v for i, v in enumerate(values) if i == 0 or v != values[i - 1]
    ]


@given(VALUES)
def test_unique(values):
    assert collect(lambda: Event.sequence(values).unique()) == list(
        dict.fromkeys(values)
    )


@given(VALUES)
def test_last(values):
    assert collect(lambda: Event.sequence(values).last()) == values[-1:]


@given(VALUES, POSITIVE)
def test_chunk(values, size):
    assert collect(lambda: Event.sequence(values).chunk(size)) == [
        values[i : i + size] for i in range(0, len(values), size)
    ]


@given(VALUES, COUNT)
def test_deque(values, count):
    expected = [
        deque(values[max(0, i + 1 - count) if count else 0 : i + 1])
        for i in range(len(values))
    ]
    assert collect(lambda: Event.sequence(values).deque(count)) == expected


@given(VALUES)
def test_list(values):
    assert collect(lambda: Event.sequence(values).list()) == [values]


@given(VALUES, VALUES)
def test_zip(a, b):
    assert collect(lambda: Event.sequence(a).zip(Event.sequence(b))) == list(zip(a, b))


@given(VALUES, VALUES)
def test_concat_keeps_each_source_whole_and_in_order(a, b):
    # concat switches to a later source as soon as it emits, so the second
    # source starts only after the first has finished.
    later = [1.0 + i for i in range(len(b))]
    result = collect(lambda: Event.sequence(a).concat(Event.sequence(b, times=later)))
    assert result == a + b


# Contracts every operator keeps


def _operators():
    """Every operator built on one source ``s`` (other inputs are fresh
    events that stay silent)."""
    return {
        "filter": lambda s: s.filter(bool),
        "skip": lambda s: s.skip(1),
        "take": lambda s: s.take(5),
        "takewhile": lambda s: s.takewhile(bool),
        "dropwhile": lambda s: s.dropwhile(bool),
        "takeuntil": lambda s: s.takeuntil(Event("notifier")),
        "constant": lambda s: s.constant(1),
        "iterate": lambda s: s.iterate(iter(range(9))),
        "count": lambda s: s.count(),
        "enumerate": lambda s: s.enumerate(),
        "timestamp": lambda s: s.timestamp(),
        "partial": lambda s: s.partial(1),
        "partial_right": lambda s: s.partial_right(1),
        "pack": lambda s: s.pack(),
        "map": lambda s: s.map(abs),
        "mergemap": lambda s: s.mergemap(lambda x: Event.sequence([x])),
        "concatmap": lambda s: s.concatmap(lambda x: Event.sequence([x])),
        "chainmap": lambda s: s.chainmap(lambda x: Event.sequence([x])),
        "switchmap": lambda s: s.switchmap(lambda x: Event.sequence([x])),
        "switch": lambda s: s.switch(Event("other")),
        "reduce": lambda s: s.reduce(operator.add),
        "min": lambda s: s.min(),
        "max": lambda s: s.max(),
        "sum": lambda s: s.sum(),
        "product": lambda s: s.product(),
        "mean": lambda s: s.mean(),
        "any": lambda s: s.any(),
        "all": lambda s: s.all(),
        "ema": lambda s: s.ema(3),
        "previous": lambda s: s.previous(),
        "pairwise": lambda s: s.pairwise(),
        "changes": lambda s: s.changes(),
        "unique": lambda s: s.unique(),
        "last": lambda s: s.last(),
        "list": lambda s: s.list(),
        "deque": lambda s: s.deque(3),
        "chunk": lambda s: s.chunk(2),
        "chunkwith": lambda s: s.chunkwith(Event("timer")),
        "chain": lambda s: s.chain(Event("other")),
        "merge": lambda s: s.merge(Event("other")),
        "concat": lambda s: s.concat(Event("other")),
        "zip": lambda s: s.zip(Event("other")),
        "ziplatest": lambda s: s.ziplatest(Event("other")),
        "delay": lambda s: s.delay(0.5),
        "timeout": lambda s: s.timeout(10),
        "throttle": lambda s: s.throttle(5, 1),
        "debounce": lambda s: s.debounce(0.5),
        "copy": lambda s: s.copy(),
        "deepcopy": lambda s: s.deepcopy(),
        "sample": lambda s: s.sample(Event("timer")),
        "end_on_error": lambda s: s.end_on_error(),
    }


OPERATORS = _operators()


@pytest.mark.parametrize("name", sorted(OPERATORS))
def test_every_operator_forwards_upstream_errors_as_source_and_error(name):
    with virtual_loop() as loop, no_logged_errors():
        source = Event("source")
        op = OPERATORS[name](source)
        seen = []
        op.error_event.connect(lambda *args: seen.append(args))
        error = ValueError("upstream")
        source.error_event.emit(source, error)
        loop.run_until_complete(asyncio.sleep(1))
        assert seen == [(source, error)], (name, seen)


@pytest.mark.parametrize("name", sorted(OPERATORS))
@given(values=VALUES)
def test_no_operator_emits_after_it_is_done(name, values):
    with virtual_loop() as loop, no_logged_errors():
        source = Event("source")
        op = OPERATORS[name](source)
        late = []
        op.connect(lambda *args: late.append(args) if op.done() else None)
        for value in values:
            source.emit(value)
        source.set_done()
        loop.run_until_complete(asyncio.sleep(1))
        for value in values:
            source.emit(value)
        loop.run_until_complete(asyncio.sleep(1))
        assert late == [], (name, late)


# Random listener operations against a model


def _listener(index, calls):
    def listener(value):
        calls.append((index, value))

    return listener


class ListenerMachine(RuleBasedStateMachine):
    """connect / disconnect / emit in any order: every emit reaches each
    connection exactly once, in connection order."""

    def __init__(self):
        super().__init__()
        self.event = Event("e")
        self.calls = []
        self.listeners = [_listener(i, self.calls) for i in range(4)]
        self.connected = []  # model: listener indexes, in connection order

    @rule(index=st.integers(0, 3))
    def connect(self, index):
        self.event.connect(self.listeners[index])
        self.connected.append(index)

    @rule(index=st.integers(0, 3))
    def disconnect(self, index):
        self.event.disconnect(self.listeners[index])
        self.connected = [i for i in self.connected if i != index]

    @rule(value=st.integers())
    def emit(self, value):
        self.calls.clear()
        self.event.emit(value)
        assert self.calls == [(i, value) for i in self.connected]

    @precondition(lambda self: self.connected)
    @rule()
    def clear(self):
        self.event.clear()
        self.connected = []

    @invariant()
    def counts_agree(self):
        assert len(self.event) == len(self.connected)


TestListeners = ListenerMachine.TestCase


# Timing, on the virtual clock. Arrival times are whole seconds and every
# duration ends in .5, so no arrival ever ties with a timer deadline (the
# order of simultaneous events is not defined by asyncio).

ARRIVALS = st.lists(st.tuples(st.integers(0, 4), st.integers(-9, 9)), max_size=15)
HALF = st.integers(0, 4).map(lambda n: n + 0.5)


def timeline(arrivals):
    """Cumulative arrival times and values from (gap, value) pairs."""
    times = list(itertools.accumulate(gap for gap, _ in arrivals))
    return [float(t) for t in times], [value for _, value in arrivals]


def stamped(build):
    """Like :func:`collect`, with each output as ``(time, value)``."""

    def with_times():
        loop = asyncio.get_event_loop()
        return build().map(lambda *args: (loop.time(), args[0] if args else NO_VALUE))

    return collect(with_times)


@given(ARRIVALS, HALF)
def test_delay_shifts_every_value_by_exactly_the_delay(arrivals, delay):
    times, values = timeline(arrivals)
    out = stamped(lambda: Event.sequence(values, times=times).delay(delay))
    assert out == [(t + delay, v) for t, v in zip(times, values)]


@given(ARRIVALS, HALF)
def test_debounce_emits_the_last_value_of_each_burst(arrivals, delay):
    times, values = timeline(arrivals)
    out = stamped(lambda: Event.sequence(values, times=times).debounce(delay))
    expected = [
        (times[i] + delay, values[i])
        for i in range(len(values))
        if i == len(values) - 1 or times[i + 1] - times[i] > delay
    ]
    assert out == expected


@given(ARRIVALS, HALF)
def test_debounce_on_first_emits_the_first_value_of_each_burst(arrivals, delay):
    times, values = timeline(arrivals)
    out = stamped(
        lambda: Event.sequence(values, times=times).debounce(delay, on_first=True)
    )
    expected = [
        (times[i], values[i])
        for i in range(len(values))
        if i == 0 or times[i] - times[i - 1] >= delay
    ]
    assert out == expected


@given(ARRIVALS, HALF)
def test_timeout_passes_values_until_a_gap_exceeds_it(arrivals, timeout):
    times, values = timeline(arrivals)
    out = stamped(lambda: Event.sequence(values, times=times).timeout(timeout))
    expected = []
    last = 0.0
    for t, v in zip(times, values):
        if t - last > timeout:
            expected.append((last + timeout, NO_VALUE))
            break
        expected.append((t, v))
        last = t
    assert out == expected


@given(ARRIVALS, st.integers(1, 5), HALF)
def test_throttle_delivers_everything_in_order_within_the_limit(
    arrivals, maximum, interval
):
    times, values = timeline(arrivals)
    out = stamped(
        lambda: Event.sequence(values, times=times).throttle(maximum, interval)
    )
    assert [v for _, v in out] == values
    allowed = max(1, maximum - 1)  # "remain under the maximum"
    emitted = [t for t, _ in out]
    for i, t in enumerate(emitted):
        in_window = [u for u in emitted[: i + 1] if u > t - interval]
        assert len(in_window) <= allowed, (t, in_window)
    for (t_out, _), t_in in zip(out, times):
        assert t_out >= t_in


@given(VALUES)
def test_unique_by_key(values):
    seen, expected = set(), []
    for v in values:  # the first value of each key
        if abs(v) not in seen:
            seen.add(abs(v))
            expected.append(v)
    assert collect(lambda: Event.sequence(values).unique(key=abs)) == expected


@given(VALUES)
def test_errors_ends_with_its_source(values):
    with virtual_loop() as loop, no_logged_errors():
        source = Event("source")
        errors = source.errors()
        seen = []
        errors.connect(lambda *args: seen.append(args))
        failure = ValueError("x")
        source.error_event.emit(source, failure)
        source.set_done()
        loop.run_until_complete(asyncio.sleep(0))
        assert seen == [(source, failure)]
        assert errors.done()


@given(VALUES)
def test_ziplatest_keeps_positions_when_a_source_has_ended(values):
    with virtual_loop(), no_logged_errors():
        ended = Event("ended")
        ended.emit("final")
        ended.set_done()
        live = Event("live")
        out = []
        zipped = ended.ziplatest(live)
        zipped.connect(lambda *args: out.append(args))
        for v in values:
            live.emit(v)
        assert out == [("final", v) for v in values]
