"""Hypothesis profiles for the property tests.

``default`` keeps the whole suite quick enough for every Python in the
test matrix; ``thorough`` (``HYPOTHESIS_PROFILE=thorough``) searches far
more examples, for a deliberate run before a release.
"""

import os

from hypothesis import settings

settings.register_profile("default", max_examples=60, deadline=None)
settings.register_profile("thorough", max_examples=1000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
