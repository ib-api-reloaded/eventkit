"""The marble examples in the join and higher-order map docstrings, run.

Each character of a marble is one time unit (``interval=1``) on the
virtual clock, so the documented output is checked exactly as drawn.
"""

import pytest

from eventkit import Event

from .property_test import virtual_loop


def run(build):
    with virtual_loop():
        return build().run()


def marble(line):
    """The marble of a docstring line such as ``source 2:    --1--|``,
    keeping its column (time) alignment with the other lines."""
    return line.split(":", 1)[1][2:]


def drawn_output(line):
    return [c for c in marble(line) if c not in "-| "]


JOINS = {
    "concat": (
        "source 1:  -a----b-----|",
        "source 2:    --1-----2-----3----4--|",
        "source 3:                 -----------x--y--|",
        "output:    -a----b---------3----4----x--y--|",
    ),
    "merge": (
        "source 1:  -a----b-------------c------d-|",
        "source 2:     ------1-----2------3--4-|",
        "source 3:      --------x----y--|",
        "output:    -a----b--1--x--2-y--c-3--4-d-|",
    ),
    "chain": (
        "source 1:  -a----b---c|",
        "source 2:        --2-----3--4|",
        "source 3:  ------------x---------y--|",
        "output:    -a----b---c2--3--4x---y--|",
    ),
    "switch": (
        "source 1:  -a----b---c-----d---|",
        "source 2:        -----------x---y-|",
        "source 3:  ---------1----2----3-----|",
        "output:    -a----b--1----2--x---y---|",
    ),
}


@pytest.mark.parametrize("name", sorted(JOINS))
def test_join_docstring_example(name):
    *sources, output = JOINS[name]

    def build():
        events = [Event.marble(marble(line), interval=1) for line in sources]
        return getattr(events[0], name)(*events[1:])

    assert run(build) == drawn_output(output)


MAPS = {
    "mergemap": (
        ["A   B    C    D", "_1   2  3    4", "__K   L     M   N"],
        ["A", "1", "K", "B", "2", "L", "3", "C", "M", "4", "D", "N"],
    ),
    "chainmap": (
        [
            "A    B    C    D           ",
            "_       1    2    3    4",
            "__                  K    L      M   N",
        ],
        ["A", "B", "C", "D", "1", "2", "3", "4", "K", "L", "M", "N"],
    ),
    "concatmap": (
        [
            "A    B    C    D",
            "_       1    2    3    4",
            "__                  K    L      M   N",
        ],
        ["A", "B", "C", "D", "3", "4", "L", "M", "N"],
    ),
    "switchmap": (
        [
            "A    B    C    D           ",
            "_                 K    L      M   N",
            "__      1    2      3    4",
        ],
        ["A", "B", "1", "2", "K", "L", "M", "N"],
    ),
}


@pytest.mark.parametrize("name", sorted(MAPS))
def test_map_docstring_example(name):
    marbles, expected = MAPS[name]

    def build():
        outer = Event.range(3, interval=1)
        return getattr(outer, name)(lambda v: Event.marble(marbles[v], interval=1))

    assert run(build) == expected


def test_examples_match_the_docstrings():
    """The examples above are the ones in the docstrings, verbatim."""
    for name, lines in JOINS.items():
        doc = getattr(Event, name).__doc__
        for line in lines:
            assert line in doc, (name, line)
    for name, (marbles, expected) in MAPS.items():
        doc = getattr(Event, name).__doc__
        for m in marbles:
            assert repr(m) in doc, (name, m)
        assert repr(expected) in doc, (name, expected)
