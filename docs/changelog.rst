Changelog
=========

Version 3.0.0
-------------

A new major version. Read :doc:`upgrading` before moving from 2.x: equality
of events, ``disconnect``, ``concat``, ``zip``, ``timestamp`` and
``enumerate`` behave differently, and Python 3.10 is no longer supported.
Version 2.2.0 was never published; its changes are part of this release.

Requirements and packaging
^^^^^^^^^^^^^^^^^^^^^^^^^^

* Python 3.11 or later; tested on CPython 3.11 to 3.15 and PyPy 3.11.
  Python 3.10 is no longer supported.
* numpy is optional: install ``aeventkit[numpy]`` for the Array operators.
  It is imported only when an Array operator is first created, and a
  missing numpy raises an ``ImportError`` naming the extra. (#7)
* Built with uv and ``uv_build`` instead of Poetry.

Events
^^^^^^

* Every event class is a slotted dataclass. Events have no ``__dict__``,
  which saves memory per event; assigning an undeclared attribute to an
  event raises ``AttributeError``. Subclasses declare their fields with
  :func:`eventkit.event_class` (see :doc:`upgrading`).
* Events compare and hash by identity. Previously two distinct events
  with the same name and state compared equal (and hashed differently),
  so joins that look sources up by equality could act on the wrong one.
* ``Event(name=...)`` works as documented; the constructor takes ``name``.
* ``disconnect()`` (and ``-=``) undoes one ``connect()``: a listener
  connected twice stays connected once. The new ``disconnect_all()``
  removes every connection of a listener. ``disconnect(op)`` detaches an
  operator attached with ``connect(op)``.
* A built-in method bound to an object (``a_list.append``) can be
  disconnected; before, it never matched and stayed connected.
* An exception raised by a listener is emitted on ``error_event`` when that
  has listeners, and logged otherwise. It used to vanish silently. (#5)
* A listener whose weak reference has died is skipped instead of being
  called without its object.
* Awaiting an event's ``done_event`` works. (#6)

Event loop
^^^^^^^^^^

* One rule for the event loop of a thread, on every supported Python: the
  running loop; else the loop set for the thread; else a new loop, set
  for the thread. A closed loop is replaced. No deprecated asyncio API is
  used and no ``DeprecationWarning`` is emitted. (#8)
* ``emit_threadsafe()`` from another thread runs the listeners on the main
  thread's loop, as documented; it used to create an unused loop in the
  calling thread and drop the value. Without a known main-thread loop it
  raises ``RuntimeError``.
* ``Event.run()``:

  * inside a running loop raises ``RuntimeError`` saying to
    ``await event.list()`` instead, unless the loop has been made
    re-entrant with ``nest_asyncio.apply()``; (#8)
  * works on sources that end on their first step (an empty sequence) and
    on events that have already ended (returns ``[]``);
  * afterwards cancels what its own pipeline left running (for example a
    source cut short by ``timeout``, or async ``map`` tasks behind a
    ``take``), including side inputs such as a ``sample`` timer and tasks
    started during the run, and nothing else: sources with other listeners
    and unrelated tasks keep running.

Operators: changed behavior
^^^^^^^^^^^^^^^^^^^^^^^^^^^

* ``concat`` follows each source until it ends, then the next, as its
  documentation always said; values a later source emits before its turn
  are dropped. It used to switch to a later source as soon as that one
  emitted. ``concatmap`` changes the same way.
* ``zip`` ends as soon as an ended source has nothing left to pair, as
  documented, instead of when every source has ended.
* ``timestamp()`` and ``enumerate()`` emit ``(t, a, b, ...)`` and
  ``(i, a, b, ...)`` for multi-argument values, as documented, instead of
  ``(t, (a, b))``.
* ``deque()`` emits a copy each time; it used to emit its live internal
  deque, so every value received changed afterwards.
* ``takeuntil(notifier)`` keeps passing values when the notifier ends
  without emitting, as documented; it used to end.
* ``errors()`` ends with its source, and stays connected without another
  reference to it.
* ``Event.wait()`` accepts any awaitable, including coroutines (they used
  to raise ``AttributeError``), and emits the result of a future that has
  already finished (it used to end without emitting).

Operators: new
^^^^^^^^^^^^^^

* ``poolmap(executor, func, *, timeout=None, chunksize=1, ordered=True)``
  runs ``func`` on the values in any ``concurrent.futures`` executor: a
  process pool for CPU-bound work, an interpreter pool (Python 3.14+) or a
  thread pool. Values can be sent in chunks; a value whose call fails emits
  its error and the rest of its chunk is unaffected; a timeout, or
  ``run()`` cleanup, cancels work the executor has not started. The
  executor stays the caller's: ``poolmap`` never shuts it down.
* ``throttle(..., inclusive=True)`` lets the sum within an interval reach
  ``maximum`` (``throttle(10, 1, inclusive=True)`` passes up to 10 per
  second). The default, ``inclusive=False``, keeps it below ``maximum`` as
  before. ``set_limit`` takes the same keyword.
* ``eventkit.event_class``: the decorator that makes an ``Event`` or ``Op``
  subclass a slotted dataclass like the built-in ones.

Operators: fixed
^^^^^^^^^^^^^^^^

* ``timeout`` passes its source values through (#12); it stops listening
  once it has fired, expires exactly at its deadline, and no longer fires
  after being attached to a source that has already ended.
* ``map`` accepts functions that return futures and tasks, not just
  coroutines (#10). A failing async function emits the error and no value,
  like a failing plain function (it also emitted a placeholder value), so
  ``run()`` raises. A task cancelled by ``run()`` cleanup is ignored
  instead of raising inside asyncio's callback.
* ``take(0)`` is done at once instead of when its source ends.
* The ``emap`` family can be attached to its source after construction
  (``source.connect(Mergemap(f))``); it raised ``AttributeError``.
* An error that reaches an operator with no error listeners is logged
  with its traceback.
* ``ArrayProd`` is exported, like the other Array operators.
* ``throttle``: a value whose cost alone reaches the limit is emitted on
  its own once the interval is clear; it used to stall the queue and raise
  ``IndexError``. Windows are released on time with one retry timer.
* ``delay`` keeps values in order and ends after the last one; it forwards
  upstream errors (it raised ``TypeError`` on each).
* ``end_on_error`` works; it called a method that did not exist.
* ``skip`` keeps emitting without another reference to it (it could be
  garbage-collected after ``count`` values), and ``skip(0)`` passes every
  value.
* ``last()`` on an empty source ends (``run()`` used to hang).
* ``unique(key=...)`` deduplicates by key; it recorded whole values.
* ``ziplatest`` keeps each value in its source's position when a source
  has already ended.
* ``chain`` forwards errors of the sources it has queued; ``concat`` and
  ``switch`` forward source errors; ``switch`` is not ended by a source it
  has switched away from, and handles a source given twice.
* Joins and the ``emap`` family (``mergemap``, ``chainmap``, ``concatmap``,
  ``switchmap``) skip sources that have already ended: they no longer end
  at once (and then keep emitting) or wait forever. The ``emap`` family
  stops listening to its source when it ends.
* ``chunkwith`` on an ended source no longer emits on timer ticks.
* ``ema(weight=0.0)`` no longer treats the weight as missing.
* ``repeat`` emits its value ``count`` times; sources are scheduled on
  the event loop's clock.
* A source collected after its loop has closed no longer raises in its
  finalizer.
* ``__version_info__`` no longer makes ``import eventkit`` fail for
  pre-release and post-release versions (``3.0.0rc1``).

Testing
^^^^^^^

* Timing tests run on a virtual-clock event loop: exact and fast.
* Property-based tests (Hypothesis) check every operator against a
  plain-Python reference model, the timing operators against exact
  output times and limits, every operator's error and end contract, and
  random listener operations. ``HYPOTHESIS_PROFILE=thorough`` runs 1000
  examples per property.
* The marble diagrams in the docstrings of the joins and the ``emap``
  family are run as tests, so they cannot drift from the code.
* The examples from the GitHub issues are regression tests.
