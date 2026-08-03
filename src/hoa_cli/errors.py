"""Stable error categories used by the crawler and its CLI."""


class HoaCliError(Exception):
    """Base class for expected crawler failures."""


class ConfigError(HoaCliError):
    """The local crawler configuration is missing or invalid."""


class AuthenticationError(HoaCliError):
    """The teaching system did not return an authenticated response."""


class TransportError(HoaCliError):
    """A request could not be completed or returned an unusable status."""


class ParseError(HoaCliError):
    """A response did not match the observed teaching-system contract."""


class ValidationError(HoaCliError):
    """Discovered or generated data violates a publication invariant."""


class PublicationError(HoaCliError):
    """A validated candidate could not be published safely."""
