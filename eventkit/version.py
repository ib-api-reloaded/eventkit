import re
from importlib.metadata import version


def release_tuple(text: str) -> tuple[int, ...]:
    """The numeric release part of a version: ``(2, 2, 0)`` for ``2.2.0``,
    ``2.2.0rc1`` and ``2.2.0.post1``."""
    release = re.match(r"\d+(?:\.\d+)*", text)
    if release is None:
        raise ValueError(f"not a version: {text!r}")
    return tuple(int(part) for part in release.group().split("."))


# string version
__version__ = version("aeventkit")

# tuple version
__version_info__ = release_tuple(__version__)
