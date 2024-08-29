from eventkit import Event

array = list(range(10))


class TestSelect:
    async def test_select(self):
        event = Event.sequence(array).filter(lambda x: x % 2)
        assert await event.list() == [x for x in array if x % 2]

    async def test_skip(self):
        event = Event.sequence(array).skip(5)
        assert await event.list() == array[5:]

    async def test_take(self):
        event = Event.sequence(array).take(5)
        assert await event.list() == array[:5]

    async def test_takewhile(self):
        event = Event.sequence(array).takewhile(lambda x: x < 5)
        assert await event.list() == array[:5]

    async def test_dropwhile(self):
        event = Event.sequence(array).dropwhile(lambda x: x < 5)
        assert await event.list() == array[5:]

    async def test_changes(self):
        array = [1, 1, 2, 1, 2, 2, 2, 3, 1, 4, 4]
        event = Event.sequence(array).changes()
        assert await event.list() == [1, 2, 1, 2, 3, 1, 4]

    async def test_unique(self):
        array = [1, 1, 2, 1, 2, 2, 2, 3, 1, 4, 4]
        event = Event.sequence(array).unique()
        assert await event.list() == [1, 2, 3, 4]

    async def test_last(self):
        event = Event.sequence(array).last()
        assert await event.list() == [9]
