from ..event import Event, event_class, logger


@event_class
class Op(Event):
    """
    Base functionality for operators.

    The Observer pattern is implemented by the following three methods::

        on_source(self, *args)
        on_source_error(self, source, error)
        on_source_done(self, source)

    The default handlers will pass along source emits, errors and done events.
    This makes ``Op`` also suitable as an identity operator.

    A source that has already ended is handled as if it ended right then
    (``on_source_done``), so an operator ends the same way either way. An
    operator whose ``on_source_done`` uses its own state therefore sets
    that state up first and attaches to the source last::

        def __init__(self, ..., source=None):
            Op.__init__(self)
            self._state = ...
            if source is not None:
                self.set_source(source)
    """

    def __init__(self, source: Event | None = None):
        Event.__init__(self)
        if source is not None:
            self.set_source(source)

    on_source = Event.emit

    def on_source_error(self, source, error):
        if self._errors_have_listeners():
            self.error_event.emit(source, error)
        else:
            # Not inside an except block: pass the error for its traceback.
            logger.error(
                "Unhandled error from %s in %s: %r", source, self, error, exc_info=error
            )

    def on_source_done(self, _source):
        self._detach()
        self.set_done()

    def set_source(self, source):
        source = Event.create(source)
        if self._source is None:
            self._source = source
            self._connect_from(source)
        else:
            self._source.set_source(source)

    def _connect_from(self, source: Event):
        if source.done():
            self.on_source_done(source)
        else:
            source.connect(
                self.on_source, self.on_source_error, self.on_source_done, keep_ref=True
            )

    def _disconnect_from(self, source: Event):
        source.disconnect(self.on_source, self.on_source_error, self.on_source_done)

    def _detach(self):
        """Stop listening to the source, if attached (e.g. on ending)."""
        if self._source is not None:
            self._disconnect_from(self._source)
            self._source = None
