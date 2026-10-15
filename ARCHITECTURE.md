# eventkit architecture

eventkit builds event-driven data pipelines on asyncio. It is published as
`aeventkit` and imported as `eventkit`, and has no runtime dependencies.

## Layout

| Module             | Contents                                                                                                     |
| ------------------ | ------------------------------------------------------------------------------------------------------------ |
| `event.py`         | `Event`, `event_class`, the listener store (`Slot`, `Slots`), `Event.run`                                    |
| `util.py`          | `NO_VALUE`, `get_event_loop`, `main_thread_loop`, `schedule_awaitable`, `timerange`                          |
| `ops/op.py`        | `Op`, the base class of every operator                                                                       |
| `ops/create.py`    | sources: `Wait`, `Aiterate`, `Sequence`, `Repeat`, `Range`, `Timer`, `Timerange`, `Marble`                   |
| `ops/transform.py` | `Map`, `PoolMap`, `Pluck`, `Enumerate`, `Chunk`, `ChunkWith`, ... and the `Emap` family                      |
| `ops/select.py`    | `Filter`, `Skip`, `Take`, `TakeWhile`, `DropWhile`, `TakeUntil`, `Changes`, `Unique`, `Last`                 |
| `ops/aggregate.py` | `Reduce` (`Min`, `Max`, `Sum`, `Product`, `Any`, `All`), `Count`, `Mean`, `Ema`, `Pairwise`, `List`, `Deque` |
| `ops/array.py`     | `Array` (a NumPy window) and its reductions (`ArrayMean`, `ArrayStd`, ...)                                   |
| `ops/combine.py`   | `Fork`, `JoinOp`, `AddableJoinOp`, `Merge`, `Chain`, `Concat`, `Switch`, `Zip`, `Ziplatest`                  |
| `ops/timing.py`    | `Delay`, `Timeout`, `Debounce`, `Throttle`, `Sample`                                                         |
| `ops/misc.py`      | `Errors`, `EndOnError`                                                                                       |

`event.py` imports the operator classes at the bottom, after `Event` is
defined, so that methods such as `Event.map` can construct them (hence
Ruff's E402 exemption).

## Events

`Event` is a dataclass with `slots=True, eq=False, repr=False, init=False,
weakref_slot=True`. Every subclass is decorated with `eventkit.event_class`,
which applies `dataclass(slots=True, eq=False, repr=False, init=False)`. So:
instances have no `__dict__` and every attribute must be declared as an
annotation; events compare and hash by identity; constructors are
hand-written (`Event.__init__(name="")`) and keep their public signatures;
events remain weakly referenceable.

An event holds a name (default: the class's qualified name), its last
emitted arguments (`value()`), a `done()` flag, and two sub-events:
`error_event` emits `(source, error)`, and `done_event` emits the event once,
when `set_done()` is first called. `NO_VALUE` is a falsy singleton meaning
"no value" (for example the value of an empty emit).

## Listeners

`connect(listener, error=None, done=None, keep_ref=False)` (or `+=`) appends
a `Slot(obj, weakref, func)`. `Event._split` decides what is stored:

- a plain function or lambda, or a built-in function such as `print`:
  `(None, function)`, held strongly;
- a bound method: `(instance, function)`;
- a built-in method bound to an object, such as `a_list.append`: the object
  and its type's method descriptor, so that a later `disconnect` matches
  even though every attribute access creates a new bound method;
- any other callable, including an `Event`: `(callable, None)`.

When the object part supports weak references and `keep_ref` is false, only
a weak reference is kept, and the slot is removed when the object is
collected. A slot whose referent is gone but not yet removed (during an
emit, or on PyPy before the weakref callback runs) is skipped. Connecting an
`Op` attaches the operator instead (`op.set_source(event)`).

`emit(*args)` calls every slot in connection order, iterating over a copy so
listeners may connect and disconnect meanwhile. A listener connected twice
is called twice. An awaitable returned by a listener is scheduled on the
loop, which is how coroutine functions work as listeners.

`disconnect` (or `-=`) undoes one `connect`: it removes the earliest
matching slot, and does nothing if there is none; `disconnect(op)` detaches
an operator attached with `connect(op)`. `disconnect_all` removes every
connection of a listener, `disconnect_obj(obj)` every listener bound to
`obj` (sub-events included), and `clear` all listeners.

## Errors

An exception raised by a listener is caught in `Slots.__call__`. If the
emitting event's `error_event` has listeners it emits `(event, error)`
there; otherwise the exception is logged on the `eventkit.event` logger. It
never propagates to the caller of `emit`.

Operators forward upstream errors unchanged, keeping the original source
(`Op.on_source_error`), or log them when nobody listens. Sources and async
maps report their own failures with themselves as source: `Wait` and
`Aiterate` emit the error and end; an async `Map` (and `PoolMap`, per value)
emits the error and no value. `run()`, `await event` and `async for`
raise the first error that reaches them.

## Operators

An `Op` is an event with a source and three handlers, whose defaults make it
an identity operator: `on_source(*args)` emits, `on_source_error(source,
error)` forwards, `on_source_done(source)` detaches and ends. `set_source`
connects the handlers with `keep_ref=True`, so a source keeps its downstream
operators alive.

Pipelines are built by method chaining (`src.map(f).filter(p)`), by passing
the source as the last constructor argument (`Filter(p, src)`), with `pipe`
or `|` (`src | Map(f) | Filter(p)`), with `src.connect(op)`, or with
`src.fork(...)` / `src[...]`, which returns a `Fork` whose `merge`, `chain`,
`concat`, `switch`, `zip` and `ziplatest` join the branches. `Event.create`
lets `pipe` and `fork` take argument-free constructors (`src[ev.Min,
ev.Max]`) and turns awaitables and async iterables into `Wait` and
`Aiterate`.

### Lifecycle rules

- An operator attached to a source that has already ended handles it as if
  the source ended then: `Op._connect_from` calls `on_source_done` instead
  of connecting.
- Therefore an operator whose end handling uses its own state (`List`,
  `Last`, `Map`, the timing operators, ...) sets that state up first and
  calls `set_source` last.
- An operator disconnects from its source and side inputs when it ends and
  emits nothing afterwards. Pending work (`Map` tasks, the `Delay` queue, a
  pending `Debounce` value, the `Throttle` queue) is delivered first.
- `Sample` and `ChunkWith` end when the source or their timer ends.
  `TakeUntil` ends when its notifier emits; a notifier that ends without
  emitting is ignored.

### Joins and higher-order maps

`Zip` and `Ziplatest` join a fixed set of sources. `Merge`, `Chain`,
`Concat` and `Switch` derive from `AddableJoinOp`: `add_source` adds a
source later, skipping one that has already ended, and a parent event
(`set_parent`) keeps the join open until the parent ends. The join ends once
no source is left and no parent can add more (`_end_if_exhausted`).

- `merge`: every value of every source, as it arrives.
- `chain`: each source whole, in order; values of later sources are queued
  until their turn (their errors are forwarded at once).
- `concat`: follows each source until it ends, then the next. Values a later
  source emits before its turn are dropped; one that ends before its turn is
  skipped.
- `switch`: follows whichever source emitted last; the source switched away
  from is disconnected and its end is not waited for.
- `zip`: the i-th emit pairs the i-th values. It ends as soon as an ended
  source has no value left to pair.
- `ziplatest`: on each emit, the latest value of every source (`NO_VALUE`
  until a source has emitted, or no emits until all have with
  `partial=False`). It ends when all sources have ended.

`Emap(constr, joiner, source)` turns each source value into an event with
`constr(*args)` and `Event.create` (an event, awaitable or async iterator)
and adds it to the joiner, whose parent is the source. `Emap` ends when the
joiner ends. `Mergemap`, `Chainmap`, `Concatmap` and `Switchmap` fix the
joiner.

### Executors

`PoolMap` (`Event.poolmap`) is a `Map` whose work runs in a
`concurrent.futures.Executor` owned by the caller. Source values are
batched into chunks of `chunksize`; each chunk is one `executor.submit` of
the module-level `_run_chunk` (picklable for process pools), which returns
an `(ok, result-or-exception)` pair per value, so one failing value does
not fail its chunk. The executor's future is wrapped with
`asyncio.wrap_future` and handled as a `Map` task: ordering, the timeout
(per chunk) and `run()` cleanup come from `Map`, and cancelling the
wrapper cancels the executor's future if it has not started.

## Timing

Timing operators use the loop clock and schedule on absolute deadlines with
`loop.call_at`.

- `Sequence`, `Range`, `Repeat` and `Timer` sleep toward `t0 + offset`, so
  delays do not accumulate. `Timerange` follows the wall clock.
- `Delay` keeps one arrival-ordered queue and one timer; each firing
  releases everything due by `max(head deadline, now)`. Errors and the end
  are delayed too. (asyncio does not order timers with equal deadlines, so
  a timer per value could reorder them.)
- `Timeout` compares the last arrival with the deadline its timer was
  scheduled for (`handle.when()`), not with the clock, which asyncio may
  read up to one tick early. On expiry it emits once with no arguments and
  ends.
- `Throttle` keeps the emit times and costs of a sliding window and at most
  one retry timer, whose deadline counts as "now" when it fires. With
  `inclusive=False` (default) a window's total cost stays below `maximum`,
  so `throttle(10, 1)` emits at most 9 per second; `inclusive=True` lets it
  reach `maximum`. A value whose cost alone exceeds the limit is emitted on
  its own once the window is empty. `set_limit` changes the limit at run
  time and `status_event` reports throttling on and off.
- `Debounce` emits the first value of a burst (`on_first=True`) or the last
  one, `delay` seconds after it.

## Event loop

`util.get_event_loop()` returns the running loop; else the loop set for the
thread; else a new loop, which it sets for the thread so asyncio and
eventkit share it. A closed loop is replaced like a missing one. It uses no
deprecated API and emits no `DeprecationWarning` on any supported version.

Main-thread calls record the loop they return. `main_thread_loop()` returns
`get_event_loop()` in the main thread and that recorded loop elsewhere; it
raises `RuntimeError` rather than create a loop nobody runs.
`emit_threadsafe` passes `emit` to it with `call_soon_threadsafe`.

`schedule_awaitable` returns futures unchanged and wraps other awaitables in
a task. Task-driven sources (`Wait`, `Aiterate` and subclasses) keep the
task in `_task` and cancel it when finalised unless its loop is closed;
`Wait` never cancels a future it was given. `await event` returns the next
emit, `async for` / `aiter()` iterate until the event ends, and
`Event.wait` / `Event.aiterate` convert the other way.

`Event.run()` runs the loop until the event ends and returns its values as a
list. It subscribes before the loop runs, so an event that has ended, or
ends on its first step, is still seen. It refuses to run inside a running
loop unless that loop is patched by `nest_asyncio`. Afterwards it cancels
only the unfinished tasks of its own pipeline, found by walking upstream
through `_source`, `_sources`, `_parent` and the side inputs `_timer`,
`_notifier` and `_joiner` (`_UPSTREAM_LINKS`). An upstream event with a
listener outside the pipeline is left running, as is unrelated work.

A new operator follows these conventions: decorate with `@event_class`,
annotate every attribute, keep side inputs under an `_UPSTREAM_LINKS` name
and tasks in `_task` / `_tasks`, add the `Event` method and the export in
`__init__.py`, and add it to the `OPERATORS` table in
`tests/property_test.py` so the shared contracts cover it.

## Testing

Timing tests run on `tests/virtual_clock.py`'s `VirtualClockLoop`, whose
selector never blocks and whose clock jumps to the next timer deadline
whenever the loop would wait. Timers fire in exactly scheduled order at
exactly scheduled times: tests are deterministic and never sleep.
`VirtualClockTestCase` (for `unittest`) and `property_test.virtual_loop()`
(per Hypothesis example) give each test a fresh loop.

- `property_test.py`: operators against plain-Python reference models;
  contracts checked for every operator in `OPERATORS` (upstream errors
  forwarded as `(source, error)`, nothing emitted after done); a state
  machine over connect / disconnect / `disconnect_all` / `clear` / emit;
  timing properties with whole-second arrivals and `.5`-second durations so
  nothing ties with a timer. Any error logged by eventkit or asyncio fails
  the test.
- `operator_property_test.py`: joins and higher-order maps driven by random
  scripts of emits and ends, checked after every step against reference
  models; side inputs, `aiter` and `run()` edge cases.
- `docstring_examples_test.py`: runs the marble diagrams of the join and
  `*map` docstrings on the virtual clock and checks that they appear
  verbatim in the docstrings.
- `design_test.py`: every event class and `Fork` is a slotted dataclass
  without an instance dict, identity-compared, weakly referenceable, with
  its constructor signature intact.
- `lifecycle_test.py`, `errors_test.py`, `loop_test.py`,
  `timing_exact_test.py` and per-module tests cover the rest.

`tests/conftest.py` registers Hypothesis profiles `default` (60 examples)
and `thorough` (1000, with `HYPOTHESIS_PROFILE=thorough`).

- `make test`: pytest on the current interpreter (`UV_PYTHON` picks
  another) with only the `test` dependency group plus extras, a 60-second
  per-test timeout, and `DeprecationWarning` as an error.
- `make lint`: `ruff check`, `ruff format --check`, mypy.
- `make test-matrix`: `releasekit matrix` runs `make test` on CPython 3.11
  to 3.15 and PyPy 3.11, each in `.venvs/py<version>`, time-boxed, logging
  to `.venvs/logs/`. Dependencies come from wheels only; a version without
  them is reported as `NO-WHEELS`. `PYTHONS="3.13 3.15"` picks a subset.

CI runs the same targets.

## Packaging

The build backend is `uv_build` with `module-name = "eventkit"` and
`module-root = ""`. `__version__` comes from the installed distribution's
metadata. The `numpy` extra (`pip install aeventkit[numpy]`) enables the
`Array` operators; without NumPy, constructing one raises `ImportError`.
Supported: CPython 3.11 to 3.15 and PyPy 3.11. Releases go through
`releasekit`, configured under `[tool.releasekit]` in `pyproject.toml`.
