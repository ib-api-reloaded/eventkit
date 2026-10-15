"""Suite-wide test settings.

Hypothesis profiles: ``default`` keeps the whole suite quick enough for
every Python in the test matrix; ``thorough``
(``HYPOTHESIS_PROFILE=thorough``) searches far more examples, for a
deliberate run before a release.

Error logs: a test fails if anything logs an error it did not expect. A
test that expects one captures it, with ``unittest``'s ``assertLogs`` or
pytest's ``caplog``; anything else (a listener exception, a task left
pending on a closed loop) is a defect, not noise.
"""

import logging
import os

import pytest
from hypothesis import settings

settings.register_profile("default", max_examples=60, deadline=None)
settings.register_profile("thorough", max_examples=1000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))


class _ErrorRecords(logging.Handler):
    def __init__(self):
        super().__init__(logging.ERROR)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture(autouse=True)
def no_unexpected_error_logs(request):
    handler = _ErrorRecords()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)
    # A test using caplog checks its own records.
    if handler.records and "caplog" not in request.fixturenames:
        messages = "\n".join(f"  {r.name}: {r.getMessage()}" for r in handler.records)
        pytest.fail(
            "unexpected error logs (a leaked task can surface in a later "
            f"test, when it is garbage-collected):\n{messages}"
        )
