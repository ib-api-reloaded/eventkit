"""Stateful test of a join operator that is referenced only through its
sources and its listener. The operator must deliver exactly the values a
model of it predicts. A garbage collection may run between any two steps.

Hypothesis picks merge, zip or ziplatest. It then emits values on either
source and collects garbage, in any order. The model is a few lines per
operator. Some interpreters, such as PyPy, collect garbage at arbitrary
times. On those interpreters, a weakly held callback inside the operator
would appear in this test as a missing value.
"""

import gc

from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from eventkit import Event
from eventkit.util import NO_VALUE

JOINS = ("merge", "zip", "ziplatest")


class JoinUnderCollection(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.source, self.other = Event(), Event()
        self.join = ""
        self.received: list[tuple] = []
        self.expected: list[tuple] = []
        self.queues: tuple[list, list] = ([], [])  # zip's waiting values
        self.latest: list = [NO_VALUE, NO_VALUE]  # ziplatest's last values

    @precondition(lambda self: not self.join)
    @rule(join=st.sampled_from(JOINS))
    def build(self, join):
        self.join = join
        joined = getattr(self.source, join)(self.other)
        joined.connect(lambda *args: self.received.append(args))
        # ``joined`` goes out of scope here. From now on, only the sources
        # and the listener hold the pipeline.

    @precondition(lambda self: self.join)
    @rule(side=st.integers(0, 1), value=st.integers(-5, 5))
    def emit(self, side, value):
        (self.source, self.other)[side].emit(value)
        if self.join == "merge":
            self.expected.append((value,))
        elif self.join == "zip":
            self.queues[side].append(value)
            if self.queues[0] and self.queues[1]:
                self.expected.append((self.queues[0].pop(0), self.queues[1].pop(0)))
        else:
            self.latest[side] = value
            self.expected.append(tuple(self.latest))

    @rule()
    def collect(self):
        gc.collect()

    @invariant()
    def delivered_matches_model(self):
        assert self.received == self.expected


TestJoinUnderCollection = JoinUnderCollection.TestCase
