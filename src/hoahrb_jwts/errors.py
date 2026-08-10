"""Stable error categories used by the ``jwts`` crawler."""


class HoahrbJwtsError(Exception):
    """Base class for expected crawler failures."""


class ConfigError(HoahrbJwtsError):
    """The local crawler configuration is missing or invalid."""


class AuthenticationError(HoahrbJwtsError):
    """The teaching system did not return an authenticated response."""


class TransportError(HoahrbJwtsError):
    """A request could not be completed or returned an unusable status."""


class ParseError(HoahrbJwtsError):
    """A response did not match the observed teaching-system contract."""


class ValidationError(HoahrbJwtsError):
    """Discovered or generated data violates a publication invariant."""


class PublicationError(HoahrbJwtsError):
    """A validated candidate could not be published safely."""
