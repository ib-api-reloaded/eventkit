"""Every event class is a slotted dataclass compared by identity."""

import dataclasses
import inspect
import sys
import weakref

import pytest

import eventkit
from eventkit import Event, event_class
from eventkit.ops.combine import Fork


def event_family():
    """Every Event class eventkit defines. ``dataclass(slots=True)``
    replaces each decorated class with a new one; the discarded original
    can linger in ``__subclasses__()`` until collected, so only the class
    its module exports under that name counts."""
    found, stack = set(), [Event]
    while stack:
        cls = stack.pop()
        module = sys.modules.get(cls.__module__)
        if (
            cls.__module__.startswith("eventkit")
            and getattr(module, cls.__name__, None) is cls
            and cls not in found
        ):
            found.add(cls)
        stack.extend(cls.__subclasses__())
    return sorted(found, key=lambda c: c.__qualname__)


@pytest.mark.parametrize("cls", [*event_family(), Fork], ids=lambda c: c.__qualname__)
def test_every_class_is_a_slotted_dataclass(cls):
    assert dataclasses.is_dataclass(cls)
    assert "__slots__" in cls.__dict__
    assert "__dict__" not in cls.__dict__


def test_events_have_no_instance_dict():
    for event in (Event("e"), Event("e").map(abs), Event("e").errors()):
        with pytest.raises(AttributeError):
            event.undeclared = 1


def test_events_are_compared_and_hashed_by_identity():
    a, b = Event("same"), Event("same")
    assert a != b
    assert a == a
    assert {a: 1, b: 2} == {a: 1, b: 2}
    assert len({a, b}) == 2


def test_events_remain_weakly_referenceable():

    event = Event("e")
    assert weakref.ref(event)() is event


def test_constructors_keep_their_signatures():
    assert Event().name() == "Event"
    assert Event("x").name() == "x"
    assert Event(name="y").name() == "y"
    params = inspect.signature(eventkit.Timer.__init__).parameters
    assert list(params) == ["self", "interval", "count"]


def test_upgrade_guide_subclass_examples():
    """docs/upgrading.rst: a decorated subclass with a declared field, and
    an undecorated subclass, which keeps working with a __dict__."""

    @event_class
    class QuoteEvent(Event):
        symbol: str

        def __init__(self, symbol: str):
            Event.__init__(self, "quote")
            self.symbol = symbol

    quote = QuoteEvent("AAPL")
    assert (quote.name(), quote.symbol) == ("quote", "AAPL")
    with pytest.raises(AttributeError):
        quote.undeclared = 1
    seen = []
    quote.connect(seen.append)
    quote.emit(1)
    assert seen == [1]

    class Legacy(Event):
        pass

    legacy = Legacy("legacy")
    legacy.anything = 1  # an undecorated subclass has a __dict__
    assert legacy.anything == 1
