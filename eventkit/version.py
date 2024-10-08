from importlib.metadata import version

# string version
__version__ = version("eventkit")

# tuple version
__version_info__ = tuple([int(x) for x in __version__.split(".")])
