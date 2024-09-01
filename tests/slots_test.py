"""Test Slot and Slots"""

import weakref

import pytest

from eventkit.event import Event, Slot, Slots


class Obj:
    """Test Object"""

    value: int

    def __call__(self):
        return self.method()

    def method(self):
        self.value = 42
        return self.value

    def error_handler(self, caller, error):
        self.value = 73
        assert isinstance(error, ValueError)

    def error(self):
        raise ValueError


def func():
    """Test Function"""
    return 42


def error_func():
    """Test error function"""
    raise ValueError


@pytest.fixture
def slots_fixture():
    """Slots fixture"""
    obj = Obj()
    _obj, meth = Event._split(obj.method)
    obj2 = Obj()
    wr = weakref.ref(obj2)
    slots = Slots()
    slots.add(_obj, None, meth)
    slots.add(None, None, func)
    slots.add(None, wr, None)
    yield slots, obj.method, wr, obj


class TestSlot:
    """Slot tests"""

    def test_slot_obj(self):
        """Test slot obj"""

        obj = Obj()
        _obj, _meth = Event._split(obj.method)
        slot_obj = Slot(_obj, None, _meth)
        #
        assert slot_obj.obj() == 42

    def test_slot_callable(self):
        """Test slot callable"""

        obj = Obj()
        slot_obj = Slot(obj, None, None)
        #
        assert slot_obj.obj() == 42

    def test_slot_func(self):
        """Test slot func"""

        slot_obj = Slot(None, None, func)
        #
        assert slot_obj.func() == 42

    def test_slot_weakref(self):
        """Test slot weakref"""
        obj = Obj()
        wr = weakref.ref(obj)
        slot1 = Slot(obj, None, None)
        slot2 = Slot(None, wr, None)
        #
        assert slot1.obj() == 42
        del obj
        assert slot2.weakref().method() == 42


class TestSlots:
    """Test Slots"""

    def test_slots_add(self):
        """Test Slots.add"""
        meth = Obj().method
        wr = weakref.ref(Obj())
        slots = Slots()
        #
        slots.add(meth, None, None)
        #
        assert len(slots.slots) == slots.count
        assert slots.slots[0].obj is meth
        #
        slots.add(None, None, func)
        #
        assert len(slots.slots) == slots.count
        assert slots.slots[-1].func is func
        #
        slots.add(None, wr, None)
        #
        assert len(slots.slots) == slots.count
        assert slots.slots[-1].weakref is wr

    def test_slots_exists(self, slots_fixture):
        """Test Slots.exists"""

        slots, meth, wr, _ = slots_fixture
        #
        _obj, _meth = Event._split(meth)
        assert slots.exists(_obj, _meth)
        assert slots.exists(wr(), None)
        assert slots.exists(None, func)

    def test_slots_remove_obj(self, slots_fixture):
        """Test Slots.remove_obj"""
        slots, meth, wr, _ = slots_fixture
        #
        _obj, _meth = Event._split(meth)
        slots.remove_obj(_obj)
        assert not slots.exists(_obj, _meth)
        assert slots.exists(wr(), None)
        #
        slots.remove_obj(wr)
        assert not slots.exists(wr, None)
        #
        assert slots.exists(None, func)

    def test_slots_remove_ref(self, slots_fixture):
        """Test Slots.remove_ref"""
        slots, meth, wr, _ = slots_fixture

        #
        _obj, _meth = Event._split(meth)
        slots.remove_ref(wr)
        assert not slots.exists(wr, None)
        assert slots.exists(_obj, _meth)
        assert slots.exists(None, func)

    def test_slots_remove(self, slots_fixture):
        """Test Slots.remove"""
        slots, meth, wr, _ = slots_fixture

        #
        slots.remove(meth, None)
        assert not slots.exists(meth, None)
        assert slots.exists(wr(), None)
        #
        slots.remove(wr, None)
        assert not slots.exists(wr, None)
        assert slots.exists(None, func)
        #
        slots.remove(None, func)
        assert not slots.exists(None, func)
        assert slots.count == len(slots.slots)

    def test_slots_clear(self, slots_fixture):
        """Test Slots.clear"""
        [slots, _, _, _] = slots_fixture
        #
        slots.clear()
        #
        assert slots.count == len(slots.slots)

    def test_slots_call(self, slots_fixture):
        """Test Slots.__call__"""
        slots, _, wr, obj = slots_fixture

        ev = Event("test_slots_call")
        slots(ev)
        assert obj.value == 42
        assert wr().value == 42

    def test_slots_call_exception(self):
        """Test Slots.__call__ with exception"""
        slots = Slots()

        event = Event("test_slots_call_exception")
        obj = Obj()
        obj2 = Obj()
        obj3 = Obj()
        event.error_event.connect(obj2.error_handler)
        slots.add(None, None, func)  # success
        slots.add(obj3.method, None, None)  # success
        slots.add(obj.error, None, None)  # fail
        slots(event)
        assert obj2.value == 73
        assert not hasattr(obj, "value")
        assert obj3.value == 42

    def test_slots_call_exception_func(self):
        """Test Slots.__call__ with exception"""
        slots = Slots()

        event = Event("test_slots_call_exception")
        obj = Obj()
        obj2 = Obj()
        obj3 = Obj()
        event.error_event.connect(obj2.error_handler)
        slots.add(None, None, func)  # success
        slots.add(obj3.method, None, None)  # success
        slots.add(None, None, error_func)  # fail
        #
        slots(event)
        # on error set obj2.value
        assert obj2.value == 73
        # on error obj.value not set
        assert not hasattr(obj, "value")
        assert obj3.value == 42

    def test_slots_call_exception_logger(self):
        """Test Slots.__call__ with exception and Event.logger"""
        slots = Slots()

        event = Event("test_slots_call_exception_logger")
        obj = Obj()
        obj2 = Obj()
        slots.add(None, None, func)  # success
        slots.add(obj2.method, None, None)  # success
        slots.add(None, None, error_func)  # fail
        #
        slots(event)
        # on error obj.value not set
        assert not hasattr(obj, "value")
        assert obj2.value == 42
