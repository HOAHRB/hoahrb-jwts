from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("hoa-cli")
except PackageNotFoundError:  # pragma: no cover - useful when importing from a raw checkout.
    __version__ = "0.0.0"
