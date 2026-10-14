"""Property-based tests for the operators that ``property_test`` does not
model: joins, higher-order maps, argument shaping, sampling, and the
edges of sources that end at once or fail.

Joins and higher-order maps are driven by random *scripts*: a list of
actions on plain events (emit a value, or end the event), played one
action at a time against a reference model, so every intermediate state
(what has been emitted, whether the operator is done) is checked and
nothing depends on timing. Operators that are about time run on the
virtual clock with whole-second arrivals and ``.5``-second side inputs,
so no arrival ever ties with a timer (asyncio does not order
simultaneous events).
"""

import asyncio
import operator
import time
from collections import deque

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from eventkit import Event
from eventkit.util import NO_VALUE

from .property_test import (
    ARRIVALS,
    VALUES,
    accumulated,
    collect,
    no_logged_errors,
    stamped,
    timeline,
    virtual_loop,
)

END = "end"  # script action: end the event (script values are ints)
OUTER = "outer"  # script target: the parent of a higher-order map

# Timer ticks for side inputs: distinct times ending in .5.
TICKS = st.lists(st.integers(0, 30), min_size=1, max_size=8, unique=True).map(
    lambda ts: [t + 0.5 for t in sorted(ts)]
)
HALF_TIME = st.integers(0, 30).map(lambda n: n + 0.5)
ARGS = st.lists(st.integers(-9, 9), max_size=4).map(tuple)


# Scripts


@st.composite
def join_scripts(draw, max_sources=3):
    """``(n, script)``: actions ``(i, value)`` / ``(i, END)`` on ``n``
    sources. A source does nothing after it ends; every source still
    open when the random part is over is ended, in order."""
    n = draw(st.integers(1, max_sources))
    raw = draw(
        st.lists(st.tuples(st.integers(0, n - 1), st.integers(-9, 11)), max_size=20)
    )
    ended, script = set(), []
    for i, v in raw:
        if i in ended:
            continue
        if v > 9:
            ended.add(i)
            script.append((i, END))
        else:
            script.append((i, v))
    script += [(i, END) for i in range(n) if i not in ended]
    return n, script


@st.composite
def emap_scripts(draw):
    """Actions on a parent (``(OUTER, value)`` creates the next nested
    event, ``(OUTER, END)`` ends the parent) and on the nested events
    created so far (``(i, value)`` / ``(i, END)``). Everything is ended
    at the end, the parent first."""
    raw = draw(
        st.lists(
            st.tuples(st.integers(0, 7), st.integers(0, 3), st.integers(-9, 9)),
            max_size=25,
        )
    )
    script, created, outer_ended, ended = [], 0, False, set()
    for kind, index, value in raw:
        if kind <= 1:
            if outer_ended or created == 4:
                continue
            script.append((OUTER, value))
            created += 1
        elif kind == 2:
            if not outer_ended:
                script.append((OUTER, END))
                outer_ended = True
        elif created:
            i = index % created
            if i in ended:
                continue
            if kind == 3:
                script.append((i, END))
                ended.add(i)
            else:
                script.append((i, value))
    if not outer_ended:
        script.append((OUTER, END))
    script += [(i, END) for i in range(created) if i not in ended]
    return script


def play(build, script, n=None):
    """Play ``script`` and return ``(emits so far, done)`` after every
    action.

    With ``n``: ``build(sources)`` joins ``n`` plain events. Without:
    ``build(parent, constr)`` is a higher-order map whose ``constr``
    returns a fresh plain event per parent value.
    """
    with virtual_loop(), no_logged_errors():
        if n is None:
            outer, nested = Event("outer"), []

            def constr(*_):
                nested.append(Event(f"nested{len(nested)}"))
                return nested[-1]

            op = build(outer, constr)
        else:
            outer, nested = None, [Event(f"source{i}") for i in range(n)]
            op = build(nested)
        out, late, snapshots = [], [], []
        op.connect(lambda *args: (late if op.done() else out).append(args))
        for who, value in script:
            target = outer if who == OUTER else nested[who]
            if value == END:
                target.set_done()
            else:
                target.emit(value)
            snapshots.append((list(out), op.done()))
        assert late == [], f"emitted after done: {late}"
        return snapshots


# Reference models of the joins. Each follows sources by index; ``add``
# registers the next source, ``emit``/``end`` replay a script action,
# ``out`` is what the join has emitted and ``following()`` the sources
# whose end the join still waits for.


class MergeModel:
    """Everything from every source, as it arrives."""

    def __init__(self):
        self.n, self.ended, self.out = 0, set(), []

    def add(self):
        self.n += 1

    def emit(self, i, v):
        self.out.append((v,))

    def end(self, i):
        self.ended.add(i)

    def following(self):
        return set(range(self.n)) - self.ended


class ChainModel(MergeModel):
    """Sources one after another, each whole: values of a source that is
    not yet the current one are queued until it is."""

    def __init__(self):
        super().__init__()
        self.values = []

    def add(self):
        super().add()
        self.values.append([])

    def emit(self, i, v):
        self.values[i].append(v)
        self._release()

    def end(self, i):
        super().end(i)
        self._release()

    def _release(self):
        # Everything of the ended sources before the current one, then
        # what the current one has sent so far.
        current = min(self.following(), default=self.n)
        self.out = [
            (x,) for j in range(min(current + 1, self.n)) for x in self.values[j]
        ]


class ConcatModel(MergeModel):
    """The ``Event.concat`` docstring: re-emit one source until it ends,
    then move to the next. Reading of its marble diagram: values a later
    source emits while an earlier one is still running are dropped (not
    queued as in ``chain``), and do not end the earlier source."""

    def emit(self, i, v):
        if all(j in self.ended for j in range(i)):
            self.out.append((v,))


class SwitchModel(MergeModel):
    """The ``Event.switch`` docstring: follow one source and move to
    another as soon as that one emits; the source switched away from is
    dropped for good. Reading: a dropped source is disconnected, so its
    end is not waited for (the marble is not precise enough to say)."""

    def __init__(self):
        super().__init__()
        self.active, self.dropped = None, set()

    def emit(self, i, v):
        if i in self.dropped:
            return
        if self.active is not None and i != self.active:
            self.dropped.add(self.active)
        self.active = i
        self.out.append((v,))

    def following(self):
        return super().following() - self.dropped


def expected(model, script, n=None):
    """The model's ``(emits so far, done)`` after every action of
    ``script`` (see :func:`play` for ``n``)."""
    parent_done = n is not None
    for _ in range(n or 0):
        model.add()
    snapshots = []
    for who, value in script:
        if who == OUTER:
            if value == END:
                parent_done = True
            else:
                model.add()
        elif value == END:
            model.end(who)
        else:
            model.emit(who, value)
        snapshots.append((list(model.out), parent_done and not model.following()))
    return snapshots


def joined(name):
    return lambda sources: getattr(sources[0], name)(*sources[1:])


# Joins


@given(join_scripts())
def test_merge_emits_everything_and_ends_with_its_last_source(case):
    n, script = case
    assert play(joined("merge"), script, n) == expected(MergeModel(), script, n)


@given(join_scripts())
def test_chain_emits_each_source_whole_in_order(case):
    n, script = case
    assert play(joined("chain"), script, n) == expected(ChainModel(), script, n)


@given(join_scripts())
def test_concat_follows_each_source_until_it_ends(case):
    n, script = case
    assert play(joined("concat"), script, n) == expected(ConcatModel(), script, n)


@given(join_scripts())
def test_switch_moves_to_whichever_source_emits(case):
    n, script = case
    assert play(joined("switch"), script, n) == expected(SwitchModel(), script, n)


def zip_model(n, script):
    """Pairs, and (reading of "ends when any source ends") done once a
    source has ended with all its values paired: nothing more can pair."""
    queues, ended, out, snapshots = [deque() for _ in range(n)], set(), [], []
    for i, v in script:
        if v == END:
            ended.add(i)
        else:
            queues[i].append(v)
            if all(queues):
                out.append(tuple(q.popleft() for q in queues))
        done = any(i in ended and not queues[i] for i in range(n))
        snapshots.append((list(out), done))
    return snapshots


@given(join_scripts())
def test_zip_pairs_the_ith_values_of_sources_of_any_length(case):
    n, script = case
    got = [out for out, _ in play(joined("zip"), script, n)]
    assert got == [out for out, _ in zip_model(n, script)]


@given(join_scripts())
def test_zip_ends_when_a_source_ends(case):
    n, script = case
    got = [done for _, done in play(joined("zip"), script, n)]
    assert got == [done for _, done in zip_model(n, script)]


@given(join_scripts(), st.booleans())
def test_ziplatest_emits_the_latest_of_every_source(case, partial):
    n, script = case
    latest, ended, out, want = [NO_VALUE] * n, set(), [], []
    for i, v in script:
        if v == END:
            ended.add(i)
        else:
            latest[i] = v
            if partial or NO_VALUE not in latest:
                out.append(tuple(latest))
        want.append((list(out), len(ended) == n))

    def build(sources):
        return sources[0].ziplatest(*sources[1:], partial=partial)

    assert play(build, script, n) == want


JOIN_WITH_ENDED_CASES = [
    pytest.param(
        "merge",
        True,
    ),
    pytest.param(
        "merge",
        False,
    ),
    pytest.param(
        "chain",
        True,
    ),
    pytest.param("chain", False),
    pytest.param(
        "concat",
        True,
    ),
    pytest.param(
        "concat",
        False,
    ),
    pytest.param(
        "switch",
        True,
    ),
    pytest.param(
        "switch",
        False,
    ),
]


@pytest.mark.parametrize(("name", "ended_first"), JOIN_WITH_ENDED_CASES)
@given(values=VALUES)
@example(values=[])
@settings(max_examples=30)
def test_join_with_an_ended_source_follows_the_live_one(name, ended_first, values):
    with virtual_loop(), no_logged_errors():
        ended, live = Event("ended"), Event("live")
        ended.set_done()
        sources = [ended, live] if ended_first else [live, ended]
        op = getattr(sources[0], name)(*sources[1:])
        out, late = [], []
        op.connect(lambda *args: (late if op.done() else out).append(args))
        for v in values:
            live.emit(v)
        assert not op.done()
        live.set_done()
        assert op.done()
        assert late == []
        assert out == [(v,) for v in values]


JOIN_ERROR_CASES = [
    "merge",
    pytest.param(
        "chain",
    ),
    "concat",
    "switch",
    "zip",
    "ziplatest",
]


@pytest.mark.parametrize("name", JOIN_ERROR_CASES)
def test_join_forwards_errors_of_a_source_that_is_not_the_first(name):
    with virtual_loop(), no_logged_errors():
        first, second = Event("first"), Event("second")
        op = getattr(first, name)(second)
        seen = []
        op.error_event.connect(lambda *args: seen.append(args))
        first.emit(1)
        error = ValueError("second")
        second.error_event.emit(second, error)
        assert seen == [(second, error)]


# Higher-order maps


@given(emap_scripts())
def test_mergemap_emits_everything_from_every_nested_event(script):
    got = play(lambda outer, constr: outer.mergemap(constr), script)
    assert got == expected(MergeModel(), script)


@given(emap_scripts())
def test_chainmap_emits_each_nested_event_whole_in_order(script):
    got = play(lambda outer, constr: outer.chainmap(constr), script)
    assert got == expected(ChainModel(), script)


@given(emap_scripts())
def test_concatmap_follows_each_nested_event_until_it_ends(script):
    got = play(lambda outer, constr: outer.concatmap(constr), script)
    assert got == expected(ConcatModel(), script)


@given(emap_scripts())
def test_switchmap_moves_to_whichever_nested_event_emits(script):
    got = play(lambda outer, constr: outer.switchmap(constr), script)
    assert got == expected(SwitchModel(), script)


EMAP_WITH_ENDED_CASES = [
    pytest.param(
        name,
    )
    for name, defect in [
        ("mergemap", "ends at once when a nested event has already ended"),
        ("chainmap", "ends at once when the first nested event has already ended"),
        ("concatmap", "waits forever for a nested event that has already ended"),
        ("switchmap", "waits forever for a nested event that has already ended"),
    ]
]


@pytest.mark.parametrize("name", EMAP_WITH_ENDED_CASES)
@given(values=VALUES)
@example(values=[])
@settings(max_examples=30)
def test_emap_skips_a_nested_event_that_has_already_ended(name, values):
    with virtual_loop(), no_logged_errors():
        ended, live = Event("ended"), Event("live")
        ended.set_done()
        nested = iter([ended, live])
        outer = Event("outer")
        op = getattr(outer, name)(lambda _: next(nested))
        out, late = [], []
        op.connect(lambda *args: (late if op.done() else out).append(args))
        outer.emit(0)
        outer.emit(1)
        outer.set_done()
        for v in values:
            live.emit(v)
        assert not op.done()
        live.set_done()
        assert op.done()
        assert late == []
        assert out == [(v,) for v in values]


async def _after_a_second(value):
    await asyncio.sleep(1)
    return value


async def _twice_a_second_apart(value):
    for k in range(2):
        await asyncio.sleep(1)
        yield (value, k)


@given(VALUES)
@settings(max_examples=30)
def test_mergemap_accepts_a_constructor_that_returns_a_coroutine(values):
    coroutines = []

    def constr(value):
        coroutines.append(_after_a_second(value))
        return coroutines[-1]

    try:
        out = collect(lambda: Event.sequence(values).mergemap(constr))
    finally:
        for coroutine in coroutines:
            coroutine.close()  # those never started, so none warns
    assert sorted(out) == sorted(values)


@given(VALUES)
@settings(max_examples=30)
def test_mergemap_accepts_a_constructor_that_returns_an_async_iterator(values):
    out = collect(lambda: Event.sequence(values).mergemap(_twice_a_second_apart))
    assert sorted(out) == sorted((v, k) for v in values for k in range(2))


# Argument shaping


def emits(build, emissions):
    """Args of every emit of ``build(source)`` while ``source`` emits
    each argument tuple of ``emissions``."""
    with virtual_loop(), no_logged_errors():
        source = Event("source")
        op = build(source)
        out = []
        op.connect(lambda *args: out.append(args))
        for args in emissions:
            source.emit(*args)
        return out


@given(st.lists(ARGS, max_size=8), ARGS)
def test_partial_pads_on_the_left(emissions, left):
    out = emits(lambda s: s.partial(*left), emissions)
    assert out == [left + args for args in emissions]


@given(st.lists(ARGS, max_size=8), ARGS)
def test_partial_right_pads_on_the_right(emissions, right):
    out = emits(lambda s: s.partial_right(*right), emissions)
    assert out == [args + right for args in emissions]


@given(st.lists(ARGS, max_size=8))
def test_pack_packs_arguments_into_one_tuple(emissions):
    assert emits(lambda s: s.pack(), emissions) == [(args,) for args in emissions]


@given(st.lists(ARGS, max_size=8))
def test_star_unpacks_a_tuple_into_arguments(emissions):
    assert emits(lambda s: s.star(), [(args,) for args in emissions]) == emissions


@given(st.lists(ARGS, max_size=8))
def test_pack_then_star_is_the_identity(emissions):
    assert emits(lambda s: s.pack().star(), emissions) == emissions


SELECTION = st.one_of(
    st.integers(-6, 6),
    st.tuples(
        st.integers(0, 5), st.sampled_from(["real", "imag", "numerator", "nope"])
    ).map(lambda t: f"{t[0]}.{t[1]}"),
)


def _pluck_model(args, selection):
    if isinstance(selection, int):
        return args[selection] if -len(args) <= selection < len(args) else NO_VALUE
    index, attr = selection.split(".")
    if int(index) >= len(args):
        return NO_VALUE
    return getattr(args[int(index)], attr, NO_VALUE)


@given(st.lists(ARGS, max_size=6), st.lists(SELECTION, max_size=4))
def test_pluck_selects_arguments_and_attributes(emissions, selections):
    out = emits(lambda s: s.pluck(*selections), emissions)
    assert out == [
        tuple(_pluck_model(args, sel) for sel in selections) for args in emissions
    ]


NESTED = st.lists(st.lists(st.integers(), max_size=3), max_size=4)


@given(st.lists(NESTED, max_size=6))
def test_copy_emits_equal_shallow_copies(values):
    with virtual_loop(), no_logged_errors():
        source = Event("source")
        out = []
        source.copy().connect(out.append)
        for value in values:
            source.emit(value)
    assert out == values
    for got, value in zip(out, values):
        assert got is not value
        assert all(a is b for a, b in zip(got, value))


@given(st.lists(NESTED, max_size=6))
def test_deepcopy_emits_equal_deep_copies(values):
    with virtual_loop(), no_logged_errors():
        source = Event("source")
        out = []
        source.deepcopy().connect(out.append)
        for value in values:
            source.emit(value)
    assert out == values
    for got, value in zip(out, values):
        assert got is not value
        assert not any(a is b for a, b in zip(got, value))


@given(st.lists(st.integers(-9, 9), max_size=8))
def test_timestamp_prepends_the_wall_clock_time(values):
    with virtual_loop(), no_logged_errors():
        source = Event("source")
        out = []
        source.timestamp().connect(lambda *args: out.append(args))
        for v in values:
            before = time.time()
            source.emit(v)
            after = time.time()
            stamp, value = out[-1]
            assert before <= stamp <= after
            assert value == v


@given(st.lists(ARGS.filter(lambda a: len(a) > 1), min_size=1, max_size=6))
def test_timestamp_prepends_the_time_to_all_arguments(emissions):
    out = emits(lambda s: s.timestamp(), emissions)
    assert [args[1:] for args in out] == emissions


@given(st.lists(ARGS.filter(lambda a: len(a) > 1), min_size=1, max_size=6))
def test_enumerate_prepends_the_count_to_all_arguments(emissions):
    out = emits(lambda s: s.enumerate(), emissions)
    assert out == [(i, *args) for i, args in enumerate(emissions)]


# Aggregates


@given(VALUES, st.integers(-3, 3))
def test_product(values, start):
    assert collect(lambda: Event.sequence(values).product(start)) == accumulated(
        values, operator.mul, start
    )


def _ema_model(values, weight):
    out = []
    for v in values:
        out.append(v if not out else (1 - weight) * out[-1] + weight * v)
    return out


def _close(got, want):
    return len(got) == len(want) and all(abs(a - b) < 1e-9 for a, b in zip(got, want))


@given(VALUES, st.integers(1, 10))
def test_ema_with_periods(values, n):
    out = collect(lambda: Event.sequence(values).ema(n))
    assert _close(out, _ema_model(values, 2 / (n + 1)))


@given(VALUES, st.floats(0.01, 1))
def test_ema_with_weight(values, weight):
    out = collect(lambda: Event.sequence(values).ema(weight=weight))
    assert _close(out, _ema_model(values, weight))


def test_ema_with_zero_weight_holds_the_first_value():
    out = collect(lambda: Event.sequence([3, 5, 7]).ema(weight=0.0))
    assert out == [3, 3, 3]


@given(ARRIVALS, st.integers(1, 6))
def test_previous_emits_the_count_th_previous_value_at_each_arrival(arrivals, count):
    times, values = timeline(arrivals)
    out = stamped(lambda: Event.sequence(values, times=times).previous(count))
    assert out == [(times[i + count], values[i]) for i in range(len(values) - count)]


@given(st.lists(ARGS.filter(bool), max_size=8), st.integers(1, 4))
def test_previous_keeps_all_arguments(emissions, count):
    assert emits(lambda s: s.previous(count), emissions) == emissions[:-count]


# Sampling and side inputs, on the virtual clock


@given(ARRIVALS, TICKS)
def test_sample_emits_the_latest_value_at_each_tick(arrivals, ticks):
    times, values = timeline(arrivals)
    out = stamped(
        lambda: Event.sequence(values, times=times).sample(
            Event.sequence(ticks, times=ticks)
        )
    )
    end = times[-1] if times else 0.0  # a sequence ends with its last value
    want = []
    for t in ticks:
        if t > end:
            break
        before = [v for s, v in zip(times, values) if s < t]
        if before:
            want.append((t, before[-1]))
    assert out == want


@given(ARRIVALS, TICKS, st.booleans())
def test_chunkwith_emits_what_arrived_since_the_previous_tick(
    arrivals, ticks, emit_empty
):
    # Reading: the chunks end with either input. When the source ends,
    # what it sent since the last tick goes out as a final chunk; when
    # the timer ends (right after its last tick), nothing more can.
    times, values = timeline(arrivals)
    out = stamped(
        lambda: Event.sequence(values, times=times).chunkwith(
            Event.sequence(ticks, times=ticks), emit_empty
        )
    )
    end = times[-1] if times else 0.0
    want, previous = [], -1.0
    for t in ticks:
        if t > end:
            rest = [v for s, v in zip(times, values) if previous < s]
            if rest:
                want.append((end, rest))
            break
        chunk = [v for s, v in zip(times, values) if previous < s < t]
        if chunk or emit_empty:
            want.append((t, chunk))
        previous = t
    assert out == want


@given(ARRIVALS, HALF_TIME)
def test_takeuntil_passes_values_until_the_notifier_emits(arrivals, at):
    times, values = timeline(arrivals)
    out = stamped(
        lambda: Event.sequence(values, times=times).takeuntil(
            Event.sequence([None], times=[at])
        )
    )
    assert out == [(t, v) for t, v in zip(times, values) if t < at]


@given(VALUES)
def test_takeuntil_keeps_going_when_the_notifier_ends_silently(values):
    with virtual_loop(), no_logged_errors():
        source, notifier = Event("source"), Event("notifier")
        op = source.takeuntil(notifier)
        out = []
        op.connect(lambda *args: out.append(args))
        notifier.set_done()
        for v in values:
            source.emit(v)
        assert not op.done()
        assert out == [(v,) for v in values]
        source.set_done()
        assert op.done()


SIDE_INPUT_OPERATORS = {
    "chunkwith": lambda source, side: source.chunkwith(side),
    "sample": lambda source, side: source.sample(side),
    "takeuntil": lambda source, side: source.takeuntil(side),
}

SIDE_INPUT_CASES = [
    pytest.param(
        "chunkwith",
    ),
    "sample",
    "takeuntil",
]


@pytest.mark.parametrize("name", SIDE_INPUT_CASES)
def test_operator_on_an_ended_source_ignores_its_side_input(name):
    with virtual_loop(), no_logged_errors():
        source, side = Event("source"), Event("side")
        source.emit(1)
        source.set_done()
        op = SIDE_INPUT_OPERATORS[name](source, side)
        out = []
        op.connect(lambda *args: out.append(args))
        side.emit("tick")
        assert op.done()
        assert out == []


# Iteration edges


@given(st.lists(st.integers(), max_size=6), st.integers(0, 9))
def test_iterate_ends_on_the_first_source_value_it_has_no_item_for(items, count):
    # Reading of "when the iterator is exhausted the event is set to be
    # done": exhaustion is only found by asking for the next item.
    with virtual_loop(), no_logged_errors():
        source = Event("source")
        op = source.iterate(iter(items))
        out = []
        op.connect(out.append)
        for k in range(count):
            source.emit(k)
        assert out == items[:count]
        assert op.done() == (count > len(items))
        source.set_done()
        assert op.done()


async def _consume(event, **kwargs):
    return [v async for v in event.aiter(**kwargs)]


async def _failing(values, error):
    for v in values:
        await asyncio.sleep(1)
        yield v
    await asyncio.sleep(1)  # fail after whoever iterates has subscribed
    raise error


async def _nothing():
    for v in ():
        yield v


def _later(values):
    return Event.sequence(values, times=[1.0 + i for i in range(len(values))])


@given(VALUES)
def test_aiter_yields_every_value_then_stops(values):
    with virtual_loop() as loop, no_logged_errors():
        assert loop.run_until_complete(_consume(_later(values))) == values


@given(VALUES)
def test_aiter_with_tuples_yields_argument_tuples(values):
    with virtual_loop() as loop, no_logged_errors():
        got = loop.run_until_complete(_consume(_later(values), tuples=True))
        assert got == [(v,) for v in values]


@given(VALUES)
def test_aiterate_of_aiter_is_the_identity(values):
    assert collect(lambda: Event.aiterate(_later(values).aiter())) == values


def test_aiter_of_an_ended_event_yields_nothing():
    with virtual_loop() as loop, no_logged_errors():
        event = Event("ended")
        event.set_done()
        assert loop.run_until_complete(_consume(event)) == []


@given(VALUES)
def test_aiter_raises_the_source_error_after_its_values(values):
    with virtual_loop() as loop, no_logged_errors():
        error = ValueError("source")
        source = Event.aiterate(_failing(values, error))
        got = []

        async def consume():
            async for v in source.aiter():
                got.append(v)

        with pytest.raises(ValueError) as raised:
            loop.run_until_complete(consume())
        assert raised.value is error
        assert got == values


# run() and list() on sources that end at once or fail

HUNG = "hung"


def bounded_run(loop, event, limit=1000.0):
    """``event.run()``, or ``HUNG`` if it has not finished after ``limit``
    virtual seconds."""
    handle = loop.call_later(limit, loop.stop)
    try:
        return event.run()
    except RuntimeError as error:
        if "stopped before Future completed" not in str(error):
            raise
        return HUNG
    finally:
        handle.cancel()


def test_run_of_an_empty_async_iterator_is_empty():
    with virtual_loop() as loop, no_logged_errors():
        assert bounded_run(loop, Event.aiterate(_nothing())) == []


def test_run_of_an_ended_event_is_empty():
    with virtual_loop() as loop, no_logged_errors():
        event = Event("ended")
        event.set_done()
        assert bounded_run(loop, event) == []


def test_run_of_wait_on_a_finished_future_is_its_result():
    with virtual_loop() as loop, no_logged_errors():
        future = loop.create_future()
        future.set_result(5)
        assert bounded_run(loop, Event.wait(future)) == [5]


def test_run_of_wait_on_a_pending_future_is_its_result():
    with virtual_loop() as loop, no_logged_errors():
        future = loop.create_future()
        loop.call_later(1, future.set_result, 5)
        assert bounded_run(loop, Event.wait(future)) == [5]


def test_run_of_wait_on_a_coroutine_is_its_result():
    coroutine = _after_a_second(5)
    try:
        with virtual_loop() as loop, no_logged_errors():
            assert bounded_run(loop, Event.wait(coroutine)) == [5]
    finally:
        coroutine.close()  # if it never started, so it does not warn


@given(VALUES)
def test_run_raises_the_error_of_a_failing_source(values):
    with virtual_loop() as loop, no_logged_errors():
        error = ValueError("source")
        source = Event.aiterate(_failing(values, error))
        seen = []
        source.connect(seen.append)
        with pytest.raises(ValueError) as raised:
            bounded_run(loop, source)
        assert raised.value is error
        assert seen == values
        assert source.done()


@given(VALUES, st.integers(-9, 9))
def test_run_raises_the_first_error_raised_inside_the_pipeline(values, bad):
    def check(x):
        if x == bad:
            raise ValueError(x)
        return x

    with virtual_loop() as loop, no_logged_errors():
        pipeline = Event.sequence(values).map(check)
        if bad in values:
            with pytest.raises(ValueError):
                bounded_run(loop, pipeline)
        else:
            assert bounded_run(loop, pipeline) == values
