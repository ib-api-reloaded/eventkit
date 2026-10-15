Upgrading from 2.x to 3.0
=========================

Most code runs unchanged. This page lists everything that can need a
change, with what to do. The :doc:`changelog` has the complete list of
changes and fixes.

Python 3.11 or later
--------------------

Python 3.10 is no longer supported. 3.0 is tested on CPython 3.11 to 3.15
and PyPy 3.11.

numpy is an extra
-----------------

aeventkit no longer installs numpy. If you use the Array operators
(``array()``, ``ArrayMean``, ``ArrayStd``, ...), install the extra::

    pip install aeventkit[numpy]

Without numpy, creating an Array operator raises ``ImportError`` naming
the extra. ``import eventkit`` does not import numpy.

Events compare by identity
--------------------------

``event_a == event_b`` is true only when they are the same event, and the
hash of an event is its identity. In 2.x, two distinct events with the same
name and state compared equal. Code that found an event in a list or dict
by an *equal* event must use the event itself.

No new attributes on events
---------------------------

Events are slotted dataclasses and have no ``__dict__``, so this raises
``AttributeError`` in 3.0::

    event = Event('quote')
    event.symbol = 'AAPL'

Keep such data next to the event (a dict keyed by the event, or a small
class holding both), or subclass with a declared field::

    from eventkit import Event, event_class

    @event_class
    class QuoteEvent(Event):
        symbol: str

        def __init__(self, symbol: str):
            Event.__init__(self, 'quote')
            self.symbol = symbol

Subclasses of ``Event`` or ``Op`` that are not decorated still work: they
get a ``__dict__`` of their own. Decorate them with ``@event_class`` and
declare their fields as class annotations (instead of ``__slots__``
tuples) to make them slotted like the built-in operators.

``disconnect`` undoes one ``connect``
-------------------------------------

A listener connected twice is called twice per emit, and 3.0 makes
``disconnect`` (and ``-=``) the exact inverse of one ``connect`` (``+=``).
In 2.x it removed every connection of the listener. To remove them all::

    event.disconnect_all(listener)

Listener exceptions are logged
------------------------------

An exception raised in a listener is emitted on ``event.error_event`` when
that has listeners, and otherwise logged by the ``eventkit.event`` logger.
In 2.x it was silently dropped in most cases. If new error logs appear
after upgrading, they are real errors in listeners that went unnoticed;
fix them, or handle them::

    event.connect(listener, error=on_error)   # on_error(event, exception)

``concat`` follows one source at a time
---------------------------------------

``concat(a, b, ...)`` emits from ``a`` until it ends, then from ``b``, and
so on; what a later source emits before its turn is dropped. In 2.x a
later source that emitted took over at once (and ``concatmap`` likewise).
Pick the join that says what you mean:

* ``chain``: every value of every source, one source after another (later
  sources' values are queued until their turn);
* ``concat``: each source in turn, from when its turn starts;
* ``switch``: whichever source emitted most recently;
* ``merge``: everything, as it arrives.

``zip`` ends as soon as no more tuples are possible
---------------------------------------------------

``zip`` ends when a source that has ended has no value left to pair, as
documented; in 2.x it waited until every source had ended. Code that
waited for a zip of a finite and an endless source to end works now.

``timestamp`` and ``enumerate`` keep all arguments
--------------------------------------------------

For an emit with several arguments, ``timestamp()`` emits
``(t, a, b, ...)`` and ``enumerate()`` emits ``(i, a, b, ...)``, as
documented. In 2.x they emitted ``(t, (a, b))``. Single-argument emits are
unchanged.

``deque`` emits copies
----------------------

Each emit of ``deque()`` is a new ``collections.deque``. In 2.x it was the
same live deque every time, so stored values kept changing.

``run()`` inside a running event loop
-------------------------------------

``event.run()`` starts the event loop, so inside a running loop (Jupyter,
IPython, any coroutine) it raises ``RuntimeError``. Await the event there::

    values = await event.list()

Code that made the loop re-entrant with ``nest_asyncio.apply()`` (or
anest-asyncio) keeps working.

``emit_threadsafe`` needs the main thread's loop
------------------------------------------------

``emit_threadsafe()`` from a worker thread schedules the emit on the main
thread's event loop, as documented. In 2.x it used a new loop in the
worker that nothing ran, so the value was lost. If the main thread has not
used an event loop through eventkit yet, it raises ``RuntimeError``.

``throttle`` limits
-------------------

Unchanged by default: the sum within any interval stays below
``maximum``, so ``throttle(10, 1)`` passes at most 9 values per second. To
allow exactly ``maximum``, use ``throttle(10, 1, inclusive=True)``.
