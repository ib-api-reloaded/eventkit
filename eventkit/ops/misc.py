from ..event import Event, event_class
from .op import Op


@event_class
class Errors(Event):
    def __init__(self, source=None):
        Event.__init__(self)
        self._source = source
        if source is None:
            return
        if source.done():
            self.set_done()
            return
        # Held strongly by the source, like an operator: ``src.errors()``
        # may have no other reference.
        source.error_event.connect(self.emit, keep_ref=True)
        source.done_event.connect(self._on_source_done, keep_ref=True)

    def _on_source_done(self, source):
        source.error_event.disconnect(self.emit)
        source.done_event.disconnect(self._on_source_done)
        self._source = None
        self.set_done()


@event_class
class EndOnError(Op):
    def __init__(self, source=None):
        Op.__init__(self, source)

    def on_source_error(self, source, error):
        self._detach()
        self.error_event.emit(source, error)
        self.set_done()
