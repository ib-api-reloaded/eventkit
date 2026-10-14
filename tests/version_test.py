from importlib.metadata import version

import pytest

import eventkit
from eventkit.version import release_tuple


def test_version_matches_the_installed_distribution() -> None:
    assert eventkit.__version__ == version("aeventkit")
    assert eventkit.__version_info__ == release_tuple(eventkit.__version__)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2.2.0", (2, 2, 0)),
        ("2.2.0rc1", (2, 2, 0)),
        ("2.2.0.post1", (2, 2, 0)),
        ("2.2.0.dev3", (2, 2, 0)),
        ("3", (3,)),
    ],
)
def test_release_tuple(text: str, expected: tuple[int, ...]) -> None:
    assert release_tuple(text) == expected


def test_release_tuple_rejects_non_versions() -> None:
    with pytest.raises(ValueError, match="not a version"):
        release_tuple("unknown")
